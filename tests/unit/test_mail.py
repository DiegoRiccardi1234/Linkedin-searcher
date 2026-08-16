"""The mailbox reader: what it recognises, what it refuses, and what it cannot do.

The tests that matter most here are not the ones about regexes. They are the
ones that check the WIRING — that the watcher is actually ticked, that the router
is actually registered, that the confirmation actually reaches ``GET /api/jobs``.
This project already paid for that lesson once: the end-of-scan audit was unit
tested as a pure function and stayed broken for weeks, because nothing tested
the code that called it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.mail.body import facts_from_body
from app.mail.matcher import (
    MailHeader,
    PendingJob,
    classify,
    company_from_sender,
    extract_application,
    is_confirmation_subject,
    is_known_sender,
    subject_facts,
)
from app.mail.watcher import MailWatcher

_NOW = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)


def _pending(job_id: int = 1, company: str = "Reply", minutes_ago: int = 30) -> PendingJob:
    return PendingJob(
        job_id=job_id,
        company=company,
        title="AI Consultant",
        opened_at=_NOW - timedelta(minutes=minutes_ago),
    )


def _header(subject: str, sender: str, minutes_ago: int = 5, **over: object) -> MailHeader:
    return MailHeader(
        key=str(over.get("key") or f"imap:1:{abs(hash(subject)) % 9999}"),
        subject=subject,
        from_addr=sender,
        message_id=str(over.get("message_id") or "<m1@example.com>"),
        date=_NOW - timedelta(minutes=minutes_ago),
        list_id=str(over.get("list_id") or ""),
    )


# ── what counts as a confirmation ────────────────────────────────────────────


def test_a_linkedin_confirmation_finds_its_offer() -> None:
    header = _header(
        "La tua candidatura è stata inviata a Reply", "jobs-noreply@linkedin.com"
    )
    result = classify(header, [_pending()])
    assert result.verdict == "match"
    assert result.job_id == 1
    assert result.rule == "subject_company"


def test_the_english_wording_works_the_same() -> None:
    header = _header("Your application was sent to Bending Spoons", "jobs-noreply@linkedin.com")
    result = classify(header, [_pending(company="Bending Spoons")])
    assert result.verdict == "match"


def test_a_job_alert_from_linkedin_is_not_a_confirmation() -> None:
    """The sender alone must never be enough: LinkedIn sends everything."""
    header = _header("Job alert: 12 nuove offerte per te", "jobs-noreply@linkedin.com")
    assert classify(header, [_pending()]).verdict == "no_match"


def test_a_rejection_is_not_a_confirmation_of_sending() -> None:
    """Reading it as one would file the offer as applied and hide that it ended."""
    header = _header(
        "Purtroppo non sei stato selezionato per la posizione", "careers@reply.com"
    )
    assert classify(header, [_pending()]).verdict == "no_match"


def test_a_confirmation_from_an_unknown_sender_needs_the_company_to_line_up() -> None:
    unrelated = _header("Grazie per la tua candidatura", "info@random-shop.example")
    assert classify(unrelated, [_pending()]).verdict == "no_match"

    from_company = _header("Grazie per la tua candidatura", "careers@reply.com")
    assert classify(from_company, [_pending()]).verdict == "match"


def test_a_lookalike_domain_is_not_a_known_sender() -> None:
    """Suffix matching, not substring: anyone can register the second one."""
    assert is_known_sender("careers@hire.lever.co")
    assert not is_known_sender("careers@notlever.co.attacker.example")


def test_two_offers_at_the_same_company_stay_unresolved() -> None:
    """Two applications to Reply in one week: the message cannot say which."""
    header = _header("La tua candidatura è stata inviata a Reply", "jobs-noreply@linkedin.com")
    result = classify(header, [_pending(1), _pending(2)])
    assert result.verdict == "ambiguous"
    assert result.job_id is None
    assert set(result.candidates) == {1, 2}


def test_a_message_that_predates_the_click_is_not_about_it() -> None:
    old = _header("La tua candidatura è stata inviata a Reply", "jobs-noreply@linkedin.com",
                  minutes_ago=180)
    assert classify(old, [_pending(minutes_ago=30)]).verdict == "no_match"
    # Clocks drift, so a few minutes the "wrong" way still counts.
    skewed = _header("La tua candidatura è stata inviata a Reply", "jobs-noreply@linkedin.com",
                     minutes_ago=33)
    assert classify(skewed, [_pending(minutes_ago=30)]).verdict == "match"


def test_a_short_company_name_does_not_swallow_a_longer_one() -> None:
    """The "bit" vs "Bitpanda" rule, reused rather than reimplemented."""
    header = _header("Grazie per la tua candidatura in Bitpanda", "careers@bitpanda.com")
    assert classify(header, [_pending(company="bit")]).verdict != "match"


# ── the three ways a real mailbox produced answers nobody could use ──────────
# Each of these was measured on one real 90-day Outlook mailbox (853 messages,
# 275 offers in the archive) before it was written: the rules produced 116
# proposals, 66 of which no human could have resolved. After these three, 50
# proposals and nothing true was lost.


def test_an_application_merely_viewed_is_not_a_confirmation_of_sending() -> None:
    """LinkedIn says "visualizzata da X" when someone OPENS it, not when it is sent.

    21 of them in one real mailbox, and not one is assignable: the regex that
    reads the employer out of a subject wants "inviata a".
    """
    header = _header(
        "La tua candidatura è stata visualizzata da Hays", "jobs-noreply@linkedin.com"
    )
    assert classify(header, [_pending(company="Hays")]).verdict == "no_match"


def test_a_subject_naming_an_employer_we_never_saved_is_not_a_proposal() -> None:
    """The message says who it is about, and it is nobody in the archive.

    Falling through to the weaker rules would look for the offer elsewhere and
    settle on one the message just said it is NOT about.
    """
    header = _header("La tua candidatura è stata inviata a Kirey", "jobs-noreply@linkedin.com")
    result = classify(header, [_pending(company="Reply")])
    assert result.verdict == "no_match"
    assert result.rule == "named_company_absent"


def test_with_no_evidence_at_all_the_answer_is_no_not_a_random_offer() -> None:
    """No employer in the subject, none in the sender, several offers open.

    The old answer was "ambiguous" carrying every pending id, and the review
    screen shows the first one — a question about Hays presented as a question
    about whatever sorted first.
    """
    header = _header("Grazie per la tua candidatura", "no-reply@myworkday.com")
    result = classify(header, [_pending(1), _pending(2, company="Accenture")])
    assert result.verdict == "no_match"
    assert result.rule == "no_evidence"
    assert result.candidates == ()


def test_one_offer_open_is_still_worth_asking_about() -> None:
    """The narrowing above must not swallow the case that IS answerable."""
    header = _header("Grazie per la tua candidatura", "no-reply@myworkday.com")
    result = classify(header, [_pending(1)])
    assert result.verdict == "ambiguous"
    assert result.rule == "single_pending"
    assert result.candidates == (1,)


def test_teamtailor_actually_sends_from_teamtailor_mail_com() -> None:
    """A list bug, not an omission: "teamtailor.com" never matches the real one."""
    assert is_known_sender("noreply@finomnia.teamtailor-mail.com")
    assert is_known_sender("candidature@join.com")


# ── the mailbox cannot be written to ─────────────────────────────────────────


class FakeIMAP4:
    """Stands in for imaplib.IMAP4_SSL and records every command received.

    ``uid_in_prefix`` picks which of the two real reply shapes to speak. Outlook
    sends the UID in the element AFTER the payload; other servers put it in the
    prefix. This fake used to invent a third shape that no server sends, which is
    how a batched reader shipped green tests and returned zero headers from a
    real mailbox.
    """

    def __init__(
        self,
        host: str,
        port: int,
        ssl_context: object = None,
        timeout: float = 0,
        uid_in_prefix: bool = False,
    ) -> None:
        self.calls: list[tuple] = []
        self.messages: dict[int, bytes] = {}
        self.uid_in_prefix = uid_in_prefix

    def login(self, user: str, password: str) -> tuple[str, list]:
        self.calls.append(("login", user))
        return ("OK", [])

    def authenticate(self, mechanism: str, authobj) -> tuple[str, list]:  # noqa: ANN001
        self.calls.append(("authenticate", mechanism, authobj(b"")))
        return ("OK", [])

    def select(self, folder: str, readonly: bool = False) -> tuple[str, list]:
        self.calls.append(("select", folder, readonly))
        return ("OK", [b"1"])

    def status(self, folder: str, what: str) -> tuple[str, list]:
        return ("OK", [b'"INBOX" (UIDVALIDITY 42)'])

    def uid(self, command: str, *args: object) -> tuple[str, list]:
        self.calls.append(("uid", command, *args))
        if command == "search":
            return ("OK", [b" ".join(str(u).encode() for u in self.messages)])
        # Transcribed from what outlook.office365.com actually sends back:
        #   (b'7913 (BODY[HEADER.FIELDS (...)] {334}', b'Date: ...')
        #   b' UID 69662)'
        # one pair per message. The UID arrives AFTER the payload, which is the
        # detail that matters and the one a hand-written fake gets wrong.
        out: list[object] = []
        for seq, raw in enumerate(str(args[0]).split(","), start=1):
            uid = int(raw)
            if uid not in self.messages:
                continue
            body = self.messages[uid]
            fields = "BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID LIST-ID REPLY-TO)]"
            if self.uid_in_prefix:
                out.append((f"{seq} (UID {uid} {fields} {{{len(body)}}}".encode(), body))
                out.append(b")")
            else:
                out.append((f"{seq} ({fields} {{{len(body)}}}".encode(), body))
                out.append(f" UID {uid})".encode())
        return ("OK", out)

    def logout(self) -> tuple[str, list]:
        self.calls.append(("logout",))
        return ("BYE", [])


_RAW = (
    b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
    b"Subject: La tua candidatura e' stata inviata a Reply\r\n"
    b"Date: Fri, 07 Aug 2026 09:57:00 +0000\r\n"
    b"Message-ID: <abc@linkedin.com>\r\n\r\n"
)


def test_the_mailbox_is_opened_read_only_and_bodies_are_never_fetched() -> None:
    """Two promises, both checked on the literal command sent to the server.

    An app that marks somebody's whole inbox as read is worse than one that does
    not work, and a body that is never downloaded cannot leak into a log.
    """
    from app.mail.config import MailAccount
    from app.mail.imap_client import ImapMailbox

    fake = FakeIMAP4("h", 993)
    fake.messages[7] = _RAW
    account = MailAccount(address="me@libero.it", host="imapmail.libero.it", secret="pw")

    with ImapMailbox(account, imap_factory=lambda *a, **k: fake) as box:
        box.select_readonly("INBOX")
        headers = list(box.fetch_headers([7]))

    assert ("select", "INBOX", True) in fake.calls
    fetches = [c for c in fake.calls if c[0] == "uid" and c[1] == "fetch"]
    assert fetches, "nothing was fetched"
    for call in fetches:
        command = " ".join(str(a) for a in call[2:])
        assert "BODY.PEEK[HEADER.FIELDS" in command
        assert "BODY[" not in command, "a body must never be requested"
    assert ("logout",) in fake.calls
    assert headers[0].subject.startswith("La tua candidatura")
    assert headers[0].key == "imap:42:7"


def test_headers_are_fetched_in_batches_not_one_round_trip_each() -> None:
    """A 365-day sweep is around 3.300 messages, and imaplib is one command each.

    One FETCH per message turned "look back a year" into an afternoon. The UID is
    read back out of each response line rather than zipped with the request:
    servers may answer in any order, and pairing by position would silently file
    every header under the wrong message.
    """
    from app.mail.config import MailAccount
    from app.mail.imap_client import _FETCH_BATCH, ImapMailbox

    fake = FakeIMAP4("h", 993)
    uids = list(range(1, 121))
    for uid in uids:
        fake.messages[uid] = _RAW
    account = MailAccount(address="me@libero.it", host="imapmail.libero.it", secret="pw")

    with ImapMailbox(account, imap_factory=lambda *a, **k: fake) as box:
        box.select_readonly("INBOX")
        headers = list(box.fetch_headers(uids))

    fetches = [c for c in fake.calls if c[0] == "uid" and c[1] == "fetch"]
    assert len(fetches) == 3, f"120 messages in batches of {_FETCH_BATCH} is three commands"
    assert len(headers) == 120, "every message still comes back"
    assert [h.key for h in headers] == [f"imap:42:{uid}" for uid in uids]
    for call in fetches:
        sent = " ".join(str(a) for a in call[2:])
        assert "BODY.PEEK[HEADER.FIELDS" in sent
        assert "BODY[" not in sent.replace("BODY.PEEK[", ""), "still headers only"


def test_the_uid_is_read_wherever_the_server_puts_it() -> None:
    """Outlook sends it after the payload, other servers in the prefix.

    Both shapes are real. Getting this wrong is silent: the reader returns
    nothing at all, and every test written against a fake that speaks the
    parser's own dialect stays green while a real mailbox yields zero headers.
    """
    from app.mail.config import MailAccount
    from app.mail.imap_client import ImapMailbox

    account = MailAccount(address="me@libero.it", host="imapmail.libero.it", secret="pw")
    for uid_in_prefix in (False, True):
        fake = FakeIMAP4("h", 993, uid_in_prefix=uid_in_prefix)
        fake.messages[11] = _RAW
        fake.messages[12] = _RAW
        with ImapMailbox(account, imap_factory=lambda *a, **k: fake) as box:
            box.select_readonly("INBOX")
            headers = list(box.fetch_headers([11, 12]))
        assert [h.key for h in headers] == ["imap:42:11", "imap:42:12"], (
            f"uid_in_prefix={uid_in_prefix}"
        )


def test_microsoft_over_imap_authenticates_with_a_token_never_a_password() -> None:
    """The only way into outlook.office365.com since September 2024.

    A password is not merely discouraged there, it is refused — so if this ever
    fell back to ``login`` the failure would look like a wrong app password and
    send the user hunting for a setting that no longer exists.
    """
    from app.mail.config import MailAccount
    from app.mail.imap_client import ImapMailbox

    fake = FakeIMAP4("h", 993)
    fake.messages[1] = _RAW
    account = MailAccount(
        address="me@outlook.com",
        auth="imap_oauth",
        host="outlook.office365.com",
        secret="refresh-token",
        client_id="cid",
    )

    with ImapMailbox(account, imap_factory=lambda *a, **k: fake, token_provider=lambda: "AT") as box:
        box.select_readonly("INBOX")

    assert not [c for c in fake.calls if c[0] == "login"], "a password must never be sent"
    auth_calls = [c for c in fake.calls if c[0] == "authenticate"]
    assert auth_calls and auth_calls[0][1] == "XOAUTH2"
    assert auth_calls[0][2] == b"user=me@outlook.com\x01auth=Bearer AT\x01\x01"
    assert ("select", "INBOX", True) in fake.calls, "read-only holds on this path too"


def test_this_package_cannot_send_mail() -> None:
    """Grep the source, because an invariant nothing enforces is a wish.

    Every other test here would stay green while the app grew the ability to
    write to the mailbox. This one would not.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    forbidden = ("import smtplib", "from smtplib")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in forbidden:
            assert needle not in text, f"{path} imports smtplib"
    mail_src = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in (root / "mail").rglob("*.py")
    )
    for verb in ('"append"', "'append'", "STORE", "EXPUNGE", '"copy"'):
        assert verb not in mail_src, f"app/mail may not issue {verb}"


# ── the wiring ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from app.main import create_app

    app = create_app(workspace_dir=tmp_path)
    with TestClient(app) as tc:
        yield tc


def test_the_mail_router_is_actually_registered(client: TestClient) -> None:
    """Catches "wrote the router, forgot the tuple in main.py"."""
    assert client.get("/api/mail/status").status_code == 200


def test_the_status_never_carries_the_secret(client: TestClient, tmp_path: Path) -> None:
    secret = "hunter2-app-password"
    saved = client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": secret},
    )
    assert saved.status_code == 200
    body = client.get("/api/mail/status").text
    assert secret not in body
    assert client.get("/api/mail/status").json()["configured"] is True
    # It is on disk, in the same file as the provider keys, and nowhere else.
    stored = json.loads((tmp_path / "data" / "local_secrets.json").read_text(encoding="utf-8"))
    assert stored["mail_secret"] == secret
    assert secret not in (tmp_path / "data" / "searcher.db").read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_a_confirmation_marks_the_offer_applied_end_to_end(
    client: TestClient, tmp_path: Path
) -> None:
    """Route in, mailbox read, archive changed, visible through the public API.

    Deliberately not a call to ``classify``: every piece of this worked in
    isolation while the chain did nothing at all, which is exactly the failure
    this project has already lived through.
    """
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    try:
        job_id, _new, _st = db.upsert_job(
            {
                "titolo": "AI, Data and Emerging Tech Consultant",
                "azienda": "Reply",
                "link": "https://www.linkedin.com/jobs/view/1",
                "descrizione": "Consulenza AI.",
            }
        )
    finally:
        db.close()

    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    assert client.post(f"/api/jobs/{job_id}/link-opened").status_code == 200

    fake = FakeIMAP4("h", 993)
    # MIME-encoded, the way LinkedIn actually sends an accented subject: this
    # also exercises the header decoding rather than assuming plain ASCII.
    encoded = _encode_subject("La tua candidatura è stata inviata a Reply")
    fake.messages[3] = (
        b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
        b"Subject: " + encoded.encode("ascii") + b"\r\n"
        b"Date: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <sent@linkedin.com>\r\n\r\n"
    )

    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    out = watcher.run_once()
    assert out["matched"] == 1, out

    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[job_id]["status"] == "applied"
    assert listed[job_id]["applied_at"]
    assert listed[job_id]["link_opened_at"] is None

    timeline = client.get(f"/api/jobs/{job_id}/timeline").json()["actions"]
    note = next(a["notes"] for a in timeline if a["action"] == "applied")
    assert note == "auto:mail:subject_company"
    assert "candidatura" not in note.lower(), "a subject must never reach the CSV export"

    # And it can be taken back, which is the price of being allowed to write.
    assert client.post(f"/api/mail/undo/{job_id}").status_code == 200
    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[job_id]["status"] == "open"


def test_the_historic_dry_run_counts_without_consuming_the_mailbox(
    client: TestClient, tmp_path: Path
) -> None:
    """Measuring a year's worth of mailbox must not spend it.

    A message the sweep files as ``no_match`` is recorded, and recorded means
    never looked at again — so a "how many is this" run that wrote those rows
    would burn the very messages a later, better rule would want to re-read.
    """
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    try:
        db.upsert_job(
            {
                "titolo": "AI Consultant",
                "azienda": "Reply",
                "link": "https://www.linkedin.com/jobs/view/9",
                "descrizione": "Consulenza AI.",
            }
        )
    finally:
        db.close()

    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )

    fake = FakeIMAP4("h", 993)
    fake.messages[3] = (
        b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
        b"Subject: " + _encode_subject("La tua candidatura è stata inviata a Reply").encode()
        + b"\r\nDate: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <sent@linkedin.com>\r\n\r\n"
    )
    fake.messages[4] = (
        b"From: Newsletter <news@example.com>\r\n"
        b"Subject: Job alert: 12 nuove offerte per te\r\n"
        b"Date: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <alert@example.com>\r\n\r\n"
    )

    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )

    events = list(watcher.run_historic(365, dry_run=True))
    done = events[-1]
    assert done["status"] == "complete"
    assert done["dry_run"] is True
    assert done["checked"] == 2, "it read the mailbox"
    assert done["proposals"] == 1, "and it counted what it found"

    seen = client.app.state.container.db.conn.execute(  # type: ignore[attr-defined]
        "SELECT COUNT(1) FROM mail_seen"
    ).fetchone()[0]
    assert seen == 0, "a dry run must not consume a single message"
    assert client.get("/api/mail/status").json()["recovery_done"] is False

    # And the real sweep afterwards still sees everything the dry run saw.
    real = list(watcher.run_historic(365))[-1]
    assert real["checked"] == 2 and real["proposals"] == 1


def test_the_recovery_reads_messages_the_routine_sweep_wrote_off(
    client: TestClient, tmp_path: Path
) -> None:
    """The sweep's "no" must not answer a question it was never asked.

    ``mail_seen`` records the sweep's verdict — "does this confirm an offer the
    archive already holds?" — and ``filter_unseen_mail`` then never fetches that
    message again. The recovery asks something else entirely: "does this record
    an application at all?". Inheriting the filter meant the routine check ate
    the recovery. On the real mailbox 673 messages had been filed as no_match
    before the import existed, and the 365-day sweep could not see one of them.
    """
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )

    fake = FakeIMAP4("h", 993)
    fake.messages[3] = (
        b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
        b"Subject: " + _encode_subject("La tua candidatura è stata inviata a Reply").encode()
        + b"\r\nDate: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <sent@linkedin.com>\r\n\r\n"
    )

    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )

    # Exactly what an earlier sweep leaves behind: the message is closed, with
    # no offer attached, because no offer from that employer was in the archive.
    db = client.app.state.container.db  # type: ignore[attr-defined]
    db.record_mail_seen(
        account="me@libero.it",
        mail_key="imap:42:3",
        verdict="no_match",
        message_id="<sent@linkedin.com>",
        matched_rule="named_company_absent",
    )

    done = list(watcher.run_historic(365))[-1]
    assert done["checked"] == 1, "a closed sweep verdict must not hide the message"
    assert done["imports"] == 1, "and the import question gets asked"

    queued = db.list_mail_review("me@libero.it")
    assert [item["kind"] for item in queued] == ["import"]

    # Run it twice: the queue is keyed by message, so nothing is proposed twice.
    list(watcher.run_historic(365))
    assert len(db.list_mail_review("me@libero.it")) == 1

    # The other side of the same line: a DECISION does close the message. Once
    # the proposal is dismissed the recovery stops fetching it, which is the
    # behaviour the queue was built for and must survive this change.
    review_id = int(db.list_mail_review("me@libero.it")[0]["id"])
    db.close_mail_review([review_id], "dismissed")
    done = list(watcher.run_historic(365))[-1]
    assert done["checked"] == 0, "a dismissed message stays dismissed"
    assert db.list_mail_review("me@libero.it") == []


def _seed_two_reply_offers(path: Path) -> None:
    from app.db import Database

    db = Database(path)
    try:
        for n in (1, 2):
            db.upsert_job(
                {
                    "titolo": f"AI Consultant {n}",
                    "azienda": "Reply",
                    "link": f"https://www.linkedin.com/jobs/view/{n}",
                    "descrizione": "Consulenza AI.",
                }
            )
    finally:
        db.close()


def _sweep(client: TestClient, subject: str, uid: int = 3) -> None:
    fake = FakeIMAP4("h", 993)
    fake.messages[uid] = (
        b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
        b"Subject: " + _encode_subject(subject).encode() + b"\r\n"
        b"Date: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <sent@linkedin.com>\r\n\r\n"
    )
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    list(watcher.run_historic(90))


def test_the_review_queue_survives_a_restart(client: TestClient, tmp_path: Path) -> None:
    """The failure this whole table exists for.

    On 7 August a real 90-day sweep put 110 proposals in a Python list, the app
    was restarted, and every one was gone — leaving a card that said "recovery
    done, 0 to review", which reads as "there was nothing".
    """
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _sweep(client, "La tua candidatura è stata inviata a Reply")

    items = client.get("/api/mail/review").json()["items"]
    assert len(items) == 1
    assert items[0]["company"] == "Reply"
    assert {c["job_id"] for c in items[0]["candidates"]}, "it names the offers it could be about"

    # The restart: a brand new watcher over the same database.
    from app.mail.watcher import MailWatcher
    from app.services.scan_control import ScanControl

    container = client.app.state.container  # type: ignore[attr-defined]
    reborn = MailWatcher(container.db, container.settings.data_dir, ScanControl())
    assert len(reborn.review_items()) == 1, "the queue outlives the process"
    assert reborn.status()["review_count"] == 1


def test_answering_a_proposal_closes_it_for_good(client: TestClient, tmp_path: Path) -> None:
    """A dismissed message used to come back on the next sweep.

    Nothing was written down about it either way, so the sweep re-proposed it
    forever. Answering now moves the row into ``mail_seen``, which is the record
    ``filter_unseen_mail`` reads.
    """
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _sweep(client, "La tua candidatura è stata inviata a Reply")
    item = client.get("/api/mail/review").json()["items"][0]

    resolved = client.post(
        "/api/mail/review/resolve", json={"attach": [], "dismiss": [item["review_id"]]}
    ).json()
    assert resolved["dismissed"] == 1
    assert client.get("/api/mail/review").json()["items"] == []

    _sweep(client, "La tua candidatura è stata inviata a Reply")
    assert client.get("/api/mail/review").json()["items"] == [], "and it stays answered"


def test_a_proposal_can_only_be_attached_to_an_offer_it_named(
    client: TestClient, tmp_path: Path
) -> None:
    """The guard that keeps a wrong click from rewriting your history.

    This endpoint writes to the record of what you applied for. A review row
    that could be about two Reply offers must not be usable to mark a third one.
    """
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    try:
        other, _n, _s = db.upsert_job(
            {
                "titolo": "Frontend Developer",
                "azienda": "Zucchetti",
                "link": "https://www.linkedin.com/jobs/view/99",
                "descrizione": "React.",
            }
        )
    finally:
        db.close()
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _sweep(client, "La tua candidatura è stata inviata a Reply")
    item = client.get("/api/mail/review").json()["items"][0]

    out = client.post(
        "/api/mail/review/resolve",
        json={"attach": [{"review_id": item["review_id"], "job_id": other}], "dismiss": []},
    ).json()
    assert out["applied"] == 0
    assert out["refused"] == [item["review_id"]]
    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[other]["status"] == "open", "an offer it never named is untouched"

    chosen = item["candidates"][0]["job_id"]
    out = client.post(
        "/api/mail/review/resolve",
        json={"attach": [{"review_id": item["review_id"], "job_id": chosen}], "dismiss": []},
    ).json()
    assert out["applied"] == 1
    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[chosen]["status"] == "applied"


def test_no_part_of_a_message_reaches_the_database(client: TestClient, tmp_path: Path) -> None:
    """The promise, checked by searching the whole file rather than trusting it.

    "No subject, no sender address, no body" is the claim this feature is sold
    on. The sentinel sits where the real risk is: LinkedIn opens every one of
    these subjects with the user's own first name — "Diego, la tua candidatura è
    stata inviata a Reply" — so the queue must keep the employer and nothing
    around it. Then every text column of every table is searched.
    """
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    sentinel = "ZZSENTINELZZ"
    _sweep(client, f"{sentinel}, la tua candidatura è stata inviata a Reply")

    assert client.get("/api/mail/review").json()["items"][0]["company"] == "Reply"

    conn = client.app.state.container.db.conn  # type: ignore[attr-defined]
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for column in columns:
            rows = conn.execute(
                f'SELECT COUNT(1) FROM "{table}" WHERE CAST("{column}" AS TEXT) LIKE ?',  # noqa: S608
                (f"%{sentinel}%",),
            ).fetchone()
            assert rows[0] == 0, f"the subject leaked into {table}.{column}"

    columns = {r[1] for r in conn.execute("PRAGMA table_info(mail_review)").fetchall()}
    assert not columns & {"subject", "from_addr", "body", "recipient"}


# ── applications the archive never knew about ────────────────────────────────


def test_extract_application_answers_a_different_question_than_classify() -> None:
    """"Which offer is this about" and "did I apply, and to whom" are not the same.

    ``classify`` must keep answering ``no_match`` when the subject names a
    company the archive has never seen — that correction stopped 39 real
    messages from marking the wrong offer. ``extract_application`` is what makes
    those 39 useful instead of merely harmless.
    """
    from app.mail.matcher import extract_application

    header = _header("La tua candidatura è stata inviata a Kirey", "jobs-noreply@linkedin.com")
    assert classify(header, [_pending(company="Reply")]).rule == "named_company_absent"

    evidence = extract_application(header)
    assert evidence is not None
    assert evidence.company == "Kirey"


def test_extract_application_still_needs_two_independent_facts() -> None:
    """Without a pending list to check against, the ATS sender is what is left.

    Drop it and any message quoting the word "candidatura" could invent an
    employer and write it into the record of what you applied for.
    """
    from app.mail.matcher import extract_application

    assert extract_application(_header("Job alert per te", "jobs-noreply@linkedin.com")) is None
    assert (
        extract_application(
            _header("La tua candidatura è stata inviata a Kirey", "amico@gmail.com")
        )
        is None
    ), "a confirmation-shaped subject from anywhere is not evidence"
    assert (
        extract_application(_header("Grazie per la tua candidatura", "no-reply@myworkday.com"))
        is None
    ), "no employer named, nothing to record"
    assert (
        extract_application(
            _header("Purtroppo non sei stato selezionato", "jobs-noreply@linkedin.com")
        )
        is None
    ), "a rejection is not a record of sending"


def test_two_applications_to_one_company_are_two_rows(tmp_path: Path) -> None:
    """The reason this does not go through ``upsert_job``.

    ``job_hash`` is sha256(title|company|link) and UNIQUE, so with an empty title
    and an empty link the second application to the same employer would collide
    with the first and silently overwrite it.
    """
    from app.db import Database

    db = Database(tmp_path / "imp.db")
    try:
        first = db.add_application_from_mail(
            company="Kirey", applied_at="2026-06-01T10:00:00+00:00", message_id="<a>", rule="import"
        )
        second = db.add_application_from_mail(
            company="Kirey", applied_at="2026-07-15T10:00:00+00:00", message_id="<b>", rule="import"
        )
        assert first is not None and second is not None and first != second

        # Same day twice is one application announced twice: LinkedIn says sent,
        # the employer's ATS says received. Measured over a year of real mail, no
        # (company, day) pair carries more than one genuine application.
        again = db.add_application_from_mail(
            company="Kirey", applied_at="2026-06-01T18:30:00+00:00", message_id="<c>", rule="import"
        )
        assert again is None

        rows = db.conn.execute(
            "SELECT titolo, azienda, fonte, status, applied_at, applied_profile_id, punteggio_ai, "
            "descrizione, link FROM jobs ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["titolo"] == "", "the mail names the employer, never the role"
            assert row["azienda"] == "Kirey"
            assert row["fonte"] == "mail"
            assert row["status"] == "applied"
            assert row["punteggio_ai"] is None, "never a score nobody gave"
            assert row["applied_profile_id"] is None, "which CV, three months ago, is not knowable"
            assert row["descrizione"] == "" and row["link"] == ""
        assert rows[0]["applied_at"].startswith("2026-06-01"), "the date of the mail, not today"
    finally:
        db.close()


def test_an_imported_application_is_never_sent_to_the_model(tmp_path: Path) -> None:
    """The guard that keeps the import from turning into a quota bill.

    "Re-score the unscored" would otherwise pick up every imported row — they are
    unscored by construction — and post sixty empty descriptions to the provider.
    """
    from app.db import Database
    from app.services.rescore_service import select_job_ids

    db = Database(tmp_path / "g.db")
    try:
        real, _n, _s = db.upsert_job(
            {
                "titolo": "Analista funzionale",
                "azienda": "BTO",
                "link": "https://example.com/1",
                "descrizione": "Analisi dei requisiti e data governance.",
            }
        )
        imported = db.add_application_from_mail(
            company="Kirey", applied_at="2026-06-01T10:00:00+00:00", message_id="<a>", rule="import"
        )
        for scope in ("unscored", "applicable", "all"):
            picked = select_job_ids(db, scope, None)
            assert imported not in picked, f"{scope} would have scored an empty description"
        assert real in select_job_ids(db, "unscored", None), "and real offers still qualify"
    finally:
        db.close()


def test_importing_from_the_queue_creates_the_application(
    client: TestClient, tmp_path: Path
) -> None:
    """End to end: mailbox read, proposal queued, user says yes, archive changes."""
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    _sweep(client, "La tua candidatura è stata inviata a Kirey")

    items = client.get("/api/mail/review").json()["items"]
    assert [i["kind"] for i in items] == ["import"]
    assert items[0]["company"] == "Kirey"
    assert items[0]["candidates"] == [], "no Kirey offer in the archive to attach to"

    out = client.post(
        "/api/mail/review/resolve",
        json={"attach": [], "create": [items[0]["review_id"]], "dismiss": []},
    ).json()
    assert len(out["created"]) == 1

    jobs = {j["azienda"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert jobs["Kirey"]["status"] == "applied"
    assert jobs["Kirey"]["punteggio_ai"] is None
    assert jobs["Kirey"]["fonte"] == "mail"
    assert client.get("/api/mail/review").json()["items"] == [], "and the question is closed"


def test_an_attach_proposal_can_be_recorded_on_its_own(
    client: TestClient, tmp_path: Path
) -> None:
    """"None of these" has to be an answer, or the record is forced to lie.

    That the archive holds offers from an employer does not make one of them the
    offer applied for. Measured on a real queue: of 53 attach proposals, the
    title read from the body matched an archive offer 15 times — the other 38
    were roles the archive had never collected. Create was refused for this kind,
    so the only answers on offer were attach to the wrong offer, or dismiss and
    lose the application entirely.
    """
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    _sweep(client, "La tua candidatura è stata inviata a Reply")

    items = client.get("/api/mail/review").json()["items"]
    assert [i["kind"] for i in items] == ["attach"]
    assert len(items[0]["candidates"]) == 2, "and neither of them need be the right one"

    out = client.post(
        "/api/mail/review/resolve",
        json={"attach": [], "create": [items[0]["review_id"]], "dismiss": []},
    ).json()
    assert out["refused"] == []
    assert len(out["created"]) == 1

    jobs = client.get("/api/jobs").json()["jobs"]
    recorded = [j for j in jobs if j["fonte"] == "mail"]
    assert len(recorded) == 1
    assert recorded[0]["azienda"] == "Reply"
    assert recorded[0]["status"] == "applied"
    assert recorded[0]["punteggio_ai"] is None, "no description, so no score"
    # And the two real offers are untouched: nothing was marked applied by guess.
    assert [j["status"] for j in jobs if j["fonte"] != "mail"] == ["open", "open"]
    assert client.get("/api/mail/review").json()["items"] == []


def test_an_application_already_in_the_archive_is_not_imported_twice(
    client: TestClient, tmp_path: Path
) -> None:
    """Third dedup level: it may already be recorded, by hand or by an earlier sweep."""
    from app.db import Database

    db = Database(tmp_path / "data" / "searcher.db")
    try:
        job_id, _n, _s = db.upsert_job(
            {
                "titolo": "Consulente applicativo",
                "azienda": "Kirey",
                "link": "https://example.com/k",
                "descrizione": "Integrazione applicativa.",
            }
        )
        db.set_job_action(job_id=job_id, action="applied", notes="manual")
    finally:
        db.close()

    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _sweep(client, "La tua candidatura è stata inviata a Kirey")

    assert client.get("/api/mail/review").json()["items"] == [], "nothing to ask: already recorded"
    assert len(client.get("/api/jobs").json()["jobs"]) == 1, "and no second row was created"


def test_the_charts_do_not_count_imports_as_a_scoring_failure(tmp_path: Path) -> None:
    """Sixty rows nobody could score would read as sixty the scorer missed."""
    from app.db import Database

    db = Database(tmp_path / "a.db")
    try:
        job_id, _n, _s = db.upsert_job(
            {
                "titolo": "Analista",
                "azienda": "BTO",
                "link": "https://example.com/1",
                "descrizione": "Analisi.",
            }
        )
        db.update_job_analysis(job_id=job_id, analysis={"punteggio": 8, "consiglio": "Candidati"})
        db.add_application_from_mail(
            company="Kirey", applied_at="2026-06-01T10:00:00+00:00", message_id="<a>", rule="import"
        )
        stats = db.get_analytics()
        assert stats["unscored"] == 0, "an application with no posting is not an unscored posting"
        assert stats["from_mail"] == 1, "but it is counted, and said out loud"
        assert stats["total"] == 2, "it is still in the archive"
        assert stats["jobs_by_status"]["applied"] == 1, "and still an application"
    finally:
        db.close()


# ── the job title, and the promise it costs ──────────────────────────────────

def _linkedin_message() -> bytes:
    """A whole confirmation: the headers the sweep reads, and the body it does not.

    Both in one blob because the fake server, like a real one, holds one message
    — what differs is which parts of it a given FETCH asks for.
    """
    return (
        b"From: LinkedIn <jobs-noreply@linkedin.com>\r\n"
        b"Subject: " + _encode_subject("La tua candidatura è stata inviata a AGM SOLUTIONS").encode()
        + b"\r\nDate: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <agm@linkedin.com>\r\n"
        b"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        b"La tua candidatura e' stata inviata a AGM SOLUTIONS\r\n"
        b"AI Developer\r\n"
        b"AGM SOLUTIONS\r\n"
        b"Italia\r\n"
        b"---------------------------------------------------------\r\n"
        b"Ora fai cosi' per avere ancora piu' successo\r\n"
        b"Visualizza offerte di lavoro simili che potrebbero interessarti\r\n"
        b"Software Engineer (Junior) - AI Systems\r\n"
        b"DAIDALOS\r\n"
    )



def test_the_role_comes_from_the_anchor_not_from_a_search() -> None:
    """Below the confirmation sit the recommendations, and they look like jobs.

    Transcribed from a real LinkedIn confirmation. Anything that goes hunting
    for "a line that reads like a job title" finds DAIDALOS's opening and files
    somebody else's job as yours. Measured on 30 real confirmations from six
    senders: 29 titles, none wrong.
    """
    from app.mail.body import role_from_body

    assert role_from_body(_linkedin_message(), "AGM SOLUTIONS") == "AI Developer"


def test_the_role_is_refused_when_the_company_does_not_corroborate_it() -> None:
    """The second independent fact: the employer has to follow the title."""
    from app.mail.body import role_from_body

    assert role_from_body(_linkedin_message(), "Reply") == ""
    assert role_from_body(b"", "AGM SOLUTIONS") == ""


def test_never_mode_issues_no_command_that_could_fetch_a_body(
    client: TestClient, tmp_path: Path
) -> None:
    """The original guarantee, still checkable on the literal IMAP command."""
    client.post(
        "/api/mail/config",
        json={
            "address": "me@libero.it",
            "auth": "password",
            "secret": "pw",
            "body_mode": "never",
        },
    )
    _seed_two_reply_offers(tmp_path / "data" / "searcher.db")
    fake = FakeIMAP4("h", 993)
    fake.messages[3] = _linkedin_message()
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    assert watcher.body_mode() == "never"
    list(watcher.run_historic(90))

    for call in fake.calls:
        sent = " ".join(str(a) for a in call)
        assert "BODY.PEEK[]" not in sent, "a whole message was downloaded in 'never' mode"

    # And the endpoint cannot be used to go round the setting.
    items = client.get("/api/mail/review").json()["items"]
    if items:
        assert (
            client.post(f"/api/mail/review/{items[0]['review_id']}/role").status_code == 409
        )


def test_a_sweep_works_on_an_empty_archive(client: TestClient, tmp_path: Path) -> None:
    """Connecting the mailbox before ever running a scan used to recover nothing.

    The sweep returned early when there was nothing to attach to, which was true
    before the import path existed: that one does not need candidates, it reads
    what the message says happened.
    """
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw", "body_mode": "never"},
    )
    fake = FakeIMAP4("h", 993)
    fake.messages[3] = _linkedin_message()
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    assert client.get("/api/jobs").json()["jobs"] == []
    done = list(watcher.run_historic(90))[-1]
    assert done["imports"] == 1
    assert client.get("/api/mail/review").json()["items"][0]["company"] == "AGM SOLUTIONS"


def test_ask_mode_reads_one_body_and_only_when_pressed(
    client: TestClient, tmp_path: Path
) -> None:
    """The default. Nothing is downloaded until a button names a message."""
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw", "body_mode": "ask"},
    )
    fake = FakeIMAP4("h", 993)
    fake.messages[3] = _linkedin_message()
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    list(watcher.run_historic(90))
    assert not any("BODY.PEEK[]" in " ".join(str(a) for a in c) for c in fake.calls), (
        "the sweep itself must not read bodies in 'ask'"
    )

    item = client.get("/api/mail/review").json()["items"][0]
    assert item["role"] == ""
    out = client.post(f"/api/mail/review/{item['review_id']}/role").json()
    assert out["role"] == "AI Developer"
    assert any("BODY.PEEK[]" in " ".join(str(a) for a in c) for c in fake.calls)

    # And it sticks, so the list does not reconnect to show it.
    assert client.get("/api/mail/review").json()["items"][0]["role"] == "AI Developer"


def test_always_mode_titles_the_application_it_creates(
    client: TestClient, tmp_path: Path
) -> None:
    client.post(
        "/api/mail/config",
        json={
            "address": "me@libero.it",
            "auth": "password",
            "secret": "pw",
            "body_mode": "always",
        },
    )
    fake = FakeIMAP4("h", 993)
    fake.messages[3] = _linkedin_message()
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    list(watcher.run_historic(90))

    item = client.get("/api/mail/review").json()["items"][0]
    assert item["role"] == "AI Developer", "read during the sweep, without being asked twice"

    client.post(
        "/api/mail/review/resolve",
        json={"attach": [], "create": [item["review_id"]], "dismiss": []},
    )
    job = next(j for j in client.get("/api/jobs").json()["jobs"] if j["azienda"] == "AGM SOLUTIONS")
    assert job["titolo"] == "AI Developer"
    assert job["punteggio_ai"] is None, "a title is not a judgement"


def test_the_scheduler_loop_actually_ticks_the_mailbox() -> None:
    """Without this, the extra_tasks wiring can vanish and every other test here
    stays green — the exact shape of the audit bug this project already had."""
    from app.services.autoscan import AutoScanScheduler

    calls: list[int] = []

    class _Stub:
        def __init__(self) -> None:
            self.db = None

    scheduler = AutoScanScheduler(
        _Stub(),  # type: ignore[arg-type]
        run_scan_fn=lambda *a, **k: iter(()),
        tick_seconds=0.01,
        extra_tasks=[lambda: calls.append(1)],
    )
    scheduler.start()
    try:
        deadline = 200
        while not calls and deadline:
            deadline -= 1
            import time as _time

            _time.sleep(0.01)
    finally:
        scheduler.shutdown()
    assert calls, "the mailbox check is never reached from the scheduler loop"


def test_nothing_is_fetched_when_no_mailbox_is_connected(tmp_path: Path) -> None:
    """What now stops the tick is an unconfigured account, not an empty archive.

    Until 2.0.1 the rule was "no offers with a link you opened from inside the
    app, no connection". It reads like restraint and behaved like an off
    switch: the flag is only set by pressing an offer's link inside Job Finder,
    so anyone applying on LinkedIn kept a mailbox that was never read. What
    replaces it is narrower and honest — an account has to be connected, and
    the window is the pending-days setting.
    """
    from app.db import Database
    from app.services.scan_control import ScanControl

    db = Database(tmp_path / "d.db")
    try:
        (tmp_path / "data").mkdir(exist_ok=True)

        def _explode(_account: object) -> object:
            raise AssertionError("the mailbox was opened with no account connected")

        watcher = MailWatcher(db, tmp_path / "data", ScanControl(), imap_factory=_explode)
        assert watcher.run_once()["status"] == "skipped"
    finally:
        db.close()


def test_a_rotated_refresh_token_is_written_back(tmp_path: Path, monkeypatch) -> None:
    """Microsoft rotates it. Not persisting works until the old one expires —
    months later, on a machine nobody touched."""
    from app.db import Database
    from app.mail import auth as mail_auth
    from app.mail import config as mail_config
    from app.services.scan_control import ScanControl

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = Database(tmp_path / "d.db")
    try:
        mail_config.save_account(
            db, data_dir, address="me@outlook.com", auth="graph",
            secret="old-refresh", client_id="cid",
        )
        import app.mail.watcher as watcher_mod

        monkeypatch.setattr(
            watcher_mod,
            "refresh_access_token",
            lambda cid, rt, scope: mail_auth.TokenBundle("access", "new-refresh", 3600),
        )
        watcher = MailWatcher(db, data_dir, ScanControl())
        account = watcher.account()
        assert account is not None
        assert watcher._oauth_token(account) == "access"
        stored = json.loads((data_dir / "local_secrets.json").read_text(encoding="utf-8"))
        assert stored["mail_secret"] == "new-refresh"
    finally:
        db.close()


def _rfc2822_now() -> str:
    from email.utils import format_datetime

    return format_datetime(datetime.now(UTC))


def _encode_subject(text: str) -> str:
    from email.header import Header

    return Header(text, "utf-8").encode()


# ── the subject says two different things: who, and what ─────────────────────
# Every case below is transcribed from a real message in a real mailbox. The
# census that produced them is in the plan: 1.743 headers over a year, of which
# 113 were confirmations the gates threw away.


def test_an_indeed_subject_names_the_role_and_not_the_employer() -> None:
    """Indeed puts the job in the subject and the employer only in the body.

    The old third alternative of ``_SUBJECT_COMPANY_RE`` captured this group and
    called it a company, so "AI SPECIALIST JUNIOR" would have been filed as an
    employer. It never fired only because the confirm gate rejected the subject
    first — one bug hidden behind another.
    """
    facts = subject_facts("Candidatura per AI SPECIALIST JUNIOR attraverso Indeed")
    assert facts.role == "AI SPECIALIST JUNIOR"
    assert facts.company == ""


def test_a_linkedin_subject_still_names_the_employer() -> None:
    facts = subject_facts("Diego, la tua candidatura e' stata inviata a aizoOn Technology Consulting")
    assert facts.company == "aizoOn Technology Consulting"
    assert facts.role == ""


def test_workday_names_the_employer_in_a_form_of_its_own() -> None:
    """Seven real messages, all from Accenture, none of them recognised."""
    assert subject_facts("Grazie per il tuo interesse nei confronti di Accenture!").company == "Accenture"


def test_an_ats_names_the_employer_after_a_dash() -> None:
    """The trailing full stop of "S.p.a." is eaten by the sentence-punctuation
    strip, which is fine and is not what this checks: what matters is that the
    name still matches the employer as stored."""
    from app.services.scan.companies import company_matches

    facts = subject_facts("Conferma ricezione candidatura - Oggi Lavoro S.p.a.")
    assert facts.role == ""
    assert company_matches(facts.company, "Oggi Lavoro S.p.a.")


@pytest.mark.parametrize(
    ("subject", "role"),
    [
        ("Candidatura eseguita con successo per AI Specialist", "AI Specialist"),
        ("Candidatura per la posizione di Manutentore software completata!", "Manutentore software"),
        ("Thank you for completing your application: AI Engineer (65)", "AI Engineer (65)"),
    ],
)
def test_agency_subjects_name_the_role(subject: str, role: str) -> None:
    assert subject_facts(subject).role == role


def test_a_reminder_to_finish_an_application_is_not_a_confirmation() -> None:
    """Oracle sends both, days apart, about the same job.

    "Complete your application for job: X" is a nudge to go back and finish.
    "Thank you for completing your application: X" is the confirmation. Reading
    the first as the second records an application that was never sent.
    """
    assert not is_confirmation_subject("Complete your application for job: AI Engineer(65)")
    assert is_confirmation_subject("Thank you for completing your application: AI Engineer (65)")


def test_indeed_is_a_known_sender_but_its_job_alerts_are_not() -> None:
    """94 of the 151 real Indeed messages are alerts, and none is a confirmation.

    They arrive from ``match.indeed.com``. The suffix rule in ``is_known_sender``
    would have swept them in along with the real confirmations, and no reject
    pattern stops a subject like "Data Analyst presso Foo srl".
    """
    assert is_known_sender("indeedapply@indeed.com")
    assert not is_known_sender("alert@match.indeed.com")


def test_a_role_bearing_subject_still_refuses_the_rejects() -> None:
    assert not is_confirmation_subject("Candidatura per Data Analyst visualizzata da Foo")


# ── reading the body: three shapes, not one ──────────────────────────────────
# Each fixture below is transcribed from a real confirmation, blank lines and
# stray separators included. A body invented to suit the parser would confirm
# the parser's own assumptions — this project has paid for that once already,
# with a FakeIMAP4 that passed every test and returned zero headers from the
# real mailbox.


def _body(lines: list[str]) -> bytes:
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "irrelevant"
    message.set_content("\n".join(lines))
    return message.as_bytes()


#: Indeed, and LinkedIn, share this shape: an anchor line, then the role, then
#: the employer. The first line is a decoy anchor — the text/plain preamble
#: matches the confirmation pattern too, and what follows it is not a job.
_INDEED_LINES = [
    "La tua candidatura e' stata inviata. Buona fortuna!",
    "Se riscontri un errore nella tua candidatura, contatta Indeed",
    "------",
    "Indeed - una ricerca. tutti i lavori. | Indeed Ireland Operations Limited",
    "We'll help you get started",
    "Candidatura inviata",
    "Sviluppatore Backend developer Web - Freelance - Piattaforma Legal Tech",
    "Studio Legale Russo Associati",
    "- Remote",
    "I seguenti elementi sono stati inviati a Studio Legale Russo Associati. In bocca al lupo!",
    "Candidatura",
    "Curriculum",
    "Cosa puoi fare ora?",
    "Visualizza offerte di lavoro simili che potrebbero interessarti",
    "Junior Data Analyst",
    "TeamSystem",
]

#: Experis: the anchor is there, but the line after it is an invitation to
#: create an account. The title sits in a labelled field further down, which is
#: exactly the case the positional rule cannot read.
_EXPERIS_LINES = [
    "Gentile Diego,",
    "Grazie per la tua candidatura. Una persona del nostro team di Recruiting la valutera' al piu' presto.",
    "Non hai ancora un account Experis? Crealo ora e accedi alla tua area personale.",
    "Job Title : AI Specialist",
    "Citta': Torino,",
    "Piemonte",
    "A presto!",
    "Il Team Experis",
]

#: Accenture, through Workday: the role is inside the sentence, and repeated in
#: a reference field below.
_ACCENTURE_LINES = [
    "Ciao Diego,",
    "Grazie per aver inviato la tua candidatura per la posizione di Junior SAP Analyst - Internship. Iniziera' la valutazione a breve.",
    "Puoi anche accedere alla tua",
    "home page",
    "Reference Role: R00279839 Junior SAP Analyst - Internship",
    "Grazie ancora per il tuo interesse!",
]


def test_an_indeed_body_names_both_the_role_and_the_employer() -> None:
    facts = facts_from_body(_body(_INDEED_LINES))
    assert facts.role == "Sviluppatore Backend developer Web - Freelance - Piattaforma Legal Tech"
    assert facts.company == "Studio Legale Russo Associati"


def test_the_recommendations_below_the_fold_are_not_your_application() -> None:
    """"Junior Data Analyst at TeamSystem" is an advert two lines further down."""
    facts = facts_from_body(_body(_INDEED_LINES))
    assert facts.role != "Junior Data Analyst"
    assert facts.company != "TeamSystem"


def test_a_labelled_field_is_read_when_the_line_after_the_anchor_is_not_a_job() -> None:
    facts = facts_from_body(_body(_EXPERIS_LINES), company="Experis")
    assert facts.role == "AI Specialist"


def test_a_role_inside_the_sentence_is_read() -> None:
    facts = facts_from_body(_body(_ACCENTURE_LINES), company="Accenture")
    assert facts.role == "Junior SAP Analyst - Internship"


def test_a_body_that_names_nothing_returns_nothing() -> None:
    facts = facts_from_body(_body(["Ciao", "Il tuo codice e': 680756", "Grazie"]))
    assert facts.role == ""
    assert facts.company == ""


def test_the_measured_linkedin_reader_is_left_alone() -> None:
    """``role_from_body`` is measured at 29 titles out of 30 and stays as it is."""
    from app.mail.body import role_from_body

    assert role_from_body(_linkedin_message(), "AGM SOLUTIONS") == "AI Developer"


# ── the tick, when nothing was opened from inside the app ────────────────────


def _tick_with(client: TestClient, raw: bytes, uid: int = 7) -> dict:
    fake = FakeIMAP4("h", 993)
    fake.messages[uid] = raw
    watcher = client.app.state.container.mailwatch  # type: ignore[attr-defined]
    from app.mail.imap_client import ImapMailbox

    watcher._imap_factory = lambda account: ImapMailbox(
        account, imap_factory=lambda *a, **k: fake
    )
    return watcher.run_once()


def _confirmation(sender: str, subject: str, body: str = "") -> bytes:
    head = (
        b"From: " + sender.encode() + b"\r\n"
        b"Subject: " + _encode_subject(subject).encode() + b"\r\n"
        b"Date: " + _rfc2822_now().encode() + b"\r\n"
        b"Message-ID: <tick@example.com>\r\n\r\n"
    )
    return head + body.encode()


def test_the_tick_still_looks_when_nothing_was_opened_from_the_app(
    client: TestClient, tmp_path: Path
) -> None:
    """The gate that quietly switched the mailbox off.

    The automatic check used to return ``idle`` without opening a connection
    unless some offer had ``link_opened_at`` set — which only happens when you
    press the link inside Job Finder. Someone who applies from LinkedIn never
    sets it, so confirmations arrived for weeks and nothing looked at them until
    a historic sweep was run by hand.
    """
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    out = _tick_with(
        client,
        _confirmation(
            "Indeed <indeedapply@indeed.com>",
            "Candidatura per AI SPECIALIST JUNIOR attraverso Indeed",
            "Candidatura inviata\nAI SPECIALIST JUNIOR\nOggi Lavoro S.p.a.\n",
        ),
    )
    assert out["status"] != "idle", "with no pending offers it used to not even connect"

    items = client.get("/api/mail/review").json()["items"]
    assert len(items) == 1, "the confirmation became a proposal"
    assert items[0]["role"] == "AI SPECIALIST JUNIOR"
    assert "Oggi Lavoro" in items[0]["company"], "the employer came out of the body"


# ── the role decides which offer the message is about ────────────────────────


def test_a_reference_code_does_not_stop_two_titles_from_matching() -> None:
    """The real pair: LinkedIn's confirmation carries the employer's own ref."""
    from app.mail.matcher import same_role

    assert same_role("Data Analytics Specialist (Rif. 2026-97)", "Data Analytics Specialist")


def test_two_different_jobs_in_the_same_field_do_not_match() -> None:
    from app.mail.matcher import same_role

    assert not same_role("Data Engineer", "Data Analytics Specialist")
    assert not same_role("AI Engineer", "Analista Funzionale")


def test_a_title_of_nothing_but_vague_words_matches_nothing() -> None:
    """"Junior Specialist" names no trade: it sits as happily on a payroll job.

    Matching on it would attach a confirmation to whichever offer happened to
    share the word, which is worse than asking.
    """
    from app.mail.matcher import same_role

    assert not same_role("Junior Specialist", "Senior Consultant")


def test_the_role_narrows_the_candidates_to_one() -> None:
    from app.mail.matcher import rank_candidates

    ordered, only = rank_candidates(
        "AI Engineer",
        [(1, "Analista Funzionale"), (2, "AI Engineer"), (3, "V&V - Test Automation Engineer")],
    )
    assert only == 2
    assert ordered[0] == 2, "the match is shown first"


def test_two_matching_titles_narrow_but_do_not_decide() -> None:
    from app.mail.matcher import rank_candidates

    ordered, only = rank_candidates("AI Engineer", [(1, "AI Engineer"), (2, "AI Engineer")])
    assert only is None, "narrowing is not deciding"
    assert set(ordered[:2]) == {1, 2}


def test_no_role_leaves_the_order_alone() -> None:
    from app.mail.matcher import rank_candidates

    ordered, only = rank_candidates("", [(1, "A"), (2, "B")])
    assert ordered == [1, 2]
    assert only is None


# ── attaching by itself, when the title leaves one answer ────────────────────


def _seed_offers(db_path: Path, rows: list[tuple[str, str]]) -> None:
    from app.db import Database

    db = Database(db_path)
    try:
        for n, (titolo, azienda) in enumerate(rows, start=1):
            db.upsert_job(
                {
                    "job_hash": f"h{n}",
                    "titolo": titolo,
                    "azienda": azienda,
                    "link": f"https://www.linkedin.com/jobs/view/{n}",
                    "descrizione": "Descrizione dell'offerta.",
                }
            )
    finally:
        db.close()


_LINKEDIN_CONFIRM_BODY = (
    "La tua candidatura e' stata inviata a Teoresi Group\n"
    "AI Engineer\n"
    "Teoresi Group\n"
    "Torino\n"
)


def test_a_title_that_leaves_one_answer_files_itself(
    client: TestClient, tmp_path: Path
) -> None:
    """Six offers at Teoresi, and the message says which one.

    This is the queue's whole reason for existing, answered: "Teoresi" alone is
    six identical radio buttons, "Teoresi + AI Engineer" is one.
    """
    _seed_offers(
        tmp_path / "data" / "searcher.db",
        [("AI Engineer", "Teoresi Group"), ("Analista Funzionale", "Teoresi Group")],
    )
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _tick_with(
        client,
        _confirmation(
            "LinkedIn <jobs-noreply@linkedin.com>",
            "La tua candidatura e' stata inviata a Teoresi Group",
            _LINKEDIN_CONFIRM_BODY,
        ),
    )
    assert client.get("/api/mail/review").json()["items"] == [], "nothing left to ask"
    applied = [
        j
        for j in client.get("/api/jobs").json()["jobs"]
        if j.get("status") == "applied"
    ]
    assert [j["titolo"] for j in applied] == ["AI Engineer"], "and it picked the right one"


def test_two_offers_the_title_fits_are_still_a_question(
    client: TestClient, tmp_path: Path
) -> None:
    """Two postings the same title fits. Narrowing six to two is worth showing
    and is not an answer, so it stays a question.

    (Two byte-identical rows cannot be built: ``dedup_key`` is title + company
    + location, so the archive would fold them into one.)
    """
    _seed_offers(
        tmp_path / "data" / "searcher.db",
        [("AI Engineer", "Teoresi Group"), ("Senior AI Engineer", "Teoresi Group")],
    )
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    _tick_with(
        client,
        _confirmation(
            "LinkedIn <jobs-noreply@linkedin.com>",
            "La tua candidatura e' stata inviata a Teoresi Group",
            _LINKEDIN_CONFIRM_BODY,
        ),
    )
    items = client.get("/api/mail/review").json()["items"]
    assert len(items) == 1, "narrowing two to two is not deciding"


def test_ask_mode_never_files_anything_by_itself(client: TestClient, tmp_path: Path) -> None:
    from app.mail import config as mail_config

    _seed_offers(
        tmp_path / "data" / "searcher.db",
        [("AI Engineer", "Teoresi Group"), ("Analista Funzionale", "Teoresi Group")],
    )
    client.post(
        "/api/mail/config",
        json={"address": "me@libero.it", "auth": "password", "secret": "pw"},
    )
    container = client.app.state.container  # type: ignore[attr-defined]
    container.db.set_preference(mail_config.PREF_ATTACH_MODE, mail_config.ATTACH_MODE_ASK)
    _tick_with(
        client,
        _confirmation(
            "LinkedIn <jobs-noreply@linkedin.com>",
            "La tua candidatura e' stata inviata a Teoresi Group",
            _LINKEDIN_CONFIRM_BODY,
        ),
    )
    items = client.get("/api/mail/review").json()["items"]
    assert len(items) == 1
    assert items[0]["suggested_job_id"], "the answer is pre-selected, not applied"


# ── three more shapes, all read off the queue this release was written for ───


def test_a_long_spontaneous_application_title_is_not_too_long_to_read() -> None:
    """105 characters, and the old ceiling was 90.

    LinkedIn confirmations for open applications carry the whole list of degrees
    in the title. The length cap was quietly dropping them — and this is the row
    that had six candidate offers and no way to tell them apart.
    """
    facts = facts_from_body(
        _body(
            [
                "La tua candidatura e' stata inviata a Teoresi Group",
                "Candidatura Spontanea - Neolaureati in Ing. Informatica/Elettronica/Meccatronica/Automazione - l. 68/99",
                "Teoresi Group",
                "Torino",
            ]
        ),
        company="Teoresi Group",
    )
    assert facts.role.startswith("Candidatura Spontanea")
    assert len(facts.role) > 90


def test_an_agency_naming_the_offer_inside_the_sentence() -> None:
    """Randstad, four times in one real queue."""
    facts = facts_from_body(
        _body(
            [
                "Ciao",
                "Diego",
                "grazie per esserti candidato all'offerta Junior Functional Safety Engineer - settore Automotive/Ferroviario CX570302.",
                "I nostri colleghi si attiveranno per valutare il tuo profilo.",
            ]
        ),
        company="Randstad Professional Italia",
    )
    assert facts.role == "Junior Functional Safety Engineer - settore Automotive/Ferroviario"


def test_the_english_apply_for_the_x_job_at_y_shape() -> None:
    facts = facts_from_body(
        _body(
            [
                "Hi Diego,",
                "Thanks for taking the time to apply for the Graduate AI software engineer job at Bending Spoons.",
                "We'll review your application and be in touch as soon as possible.",
            ]
        ),
        company="Bending Spoons",
    )
    assert facts.role == "Graduate AI software engineer"


def test_a_title_that_shrinks_to_one_generic_word_is_not_a_match() -> None:
    """Caught on the real queue, one step before it became an auto-attach.

    "IT Specialist (F/M/NB)" loses the bracket and the vague word and is left
    with {it} — a single token that sits inside almost any technical title. It
    was being matched against "Junior IT Infrastructure specialist", two
    different jobs at two different companies, and offered as the answer.

    Two meaningful words is the floor. It costs the odd real match ("Fraud
    Analyst" reduces to {fraud}) and those stay questions, which is the cheap
    side of this trade: an unanswered question is a row the user reads, a wrong
    answer is a false entry in their own history.
    """
    from app.mail.matcher import same_role

    assert not same_role("Junior IT Infrastructure specialist", "IT Specialist (F/M/NB)")
    # And the pair it must keep matching:
    assert same_role("Data Analytics Specialist (Rif. 2026-97)", "Data Analytics Specialist")


# ── the last nine shapes, and the rejection hiding among them ────────────────
# Read off the same mailbox: 59 messages that read like confirmations and
# produced no evidence, 34 because the subject named neither party and 25
# because the sender was not on any list.


def test_a_rejection_worded_as_thanks_is_not_a_confirmation() -> None:
    """Workday: "Grazie per la tua candidatura MA al momento non coincide…".

    It opens with the same four words as a real confirmation and it is a no.
    Filing it as an application sent would leave the offer waiting for an
    answer that already came.
    """
    assert not is_confirmation_subject(
        "Grazie per la tua candidatura ma al momento non coincide con le nostre posizioni aperte"
    )


@pytest.mark.parametrize(
    ("subject", "company"),
    [
        ("Grazie per la tua candidatura con Gi Group", "Gi Group"),
        ("Grazie per la tua candidatura con Wyser", "Wyser"),
        ("La tua candidatura presso Loacker", "Loacker"),
        ("Grazie per la candidatura in Business Changers", "Business Changers"),
        ("Thank you for your application to ION Group", "ION Group"),
        ("Thank you for applying to Schneider Electric!", "Schneider Electric"),
        ("Capgemini Group - New Job Application Received", "Capgemini Group"),
        ("Spindox_Candidatura ricevuta", "Spindox"),
    ],
)
def test_the_employer_shapes_that_were_being_missed(subject: str, company: str) -> None:
    assert subject_facts(subject).company == company


@pytest.mark.parametrize(
    ("subject", "role"),
    [
        ("Conferma candidatura BACK END SOFTWARE DEVELOPER - Orienta", "BACK END SOFTWARE DEVELOPER"),
        ("Conferma candidatura Help desk CX558653", "Help desk"),
        ("Application received: Fraud Analyst - View your match score", "Fraud Analyst"),
    ],
)
def test_the_role_shapes_that_were_being_missed(subject: str, role: str) -> None:
    assert subject_facts(subject).role == role


def test_a_subject_naming_both_gives_the_role_and_keeps_the_employer() -> None:
    """Workday for Dedalus: "Thank you for applying for the role of X at Y"."""
    facts = subject_facts("Thank you for applying for the role of Delivery Specialist at Dedalus")
    assert facts.role == "Delivery Specialist"
    assert facts.company == "Dedalus"


def test_a_bare_confirmation_still_names_nobody() -> None:
    """matchguru's "Conferma candidatura" and allibo's "Candidatura ricevuta"
    genuinely say nothing. Inventing an employer for them is the failure mode
    this whole package is built to avoid."""
    assert subject_facts("Conferma candidatura").company == ""
    assert subject_facts("Conferma candidatura").role == ""
    assert subject_facts("Candidatura ricevuta").company == ""


def test_an_agency_writing_from_its_own_domain_is_evidence_enough() -> None:
    """Experis, Randstad, Orienta: the confirmation is real, the subject names
    the job, and the employer is the sender itself. Requiring membership of the
    platform list dropped 25 real applications."""
    header = _header(
        "Candidatura eseguita con successo per AI Specialist", "noreply@experis.it"
    )
    evidence = extract_application(header)
    assert evidence is not None
    assert evidence.role == "AI Specialist"
    assert "experis" in evidence.company.lower()


def test_a_generic_mail_host_never_becomes_an_employer() -> None:
    header = _header("Grazie per la tua candidatura", "someone@gmail.com")
    assert extract_application(header) is None


def test_an_ats_domain_is_not_read_as_the_employer() -> None:
    """From myworkday.com the employer is the tenant's client, never "Workday"."""
    header = _header("Grazie per la candidatura", "no-reply@myworkday.com")
    evidence = extract_application(header)
    assert evidence is None or "workday" not in evidence.company.lower()


# ── what reading the extraction by hand caught, one row at a time ────────────


def test_a_subject_that_says_the_word_advert_names_no_job() -> None:
    """"Conferma di candidatura all'annuncio" — the pattern was capturing
    "all'annuncio" and offering it as a job title."""
    assert subject_facts("Conferma di candidatura all'annuncio").role == ""
    assert subject_facts("Conferma candidatura alla posizione").role == ""


def test_a_reference_number_is_not_a_job_title() -> None:
    """"Candidatura per la posizione JN -062026-740365 completata!" — a filing
    number with the tail stripped still looks like a title to a regex."""
    assert subject_facts("Candidatura per la posizione JN -062026-740365 completata!").role == ""


def test_an_ats_domain_never_supplies_the_employer() -> None:
    """Three that slipped through: the sender was an applicant tracking system
    the list did not have, so its own name was read as the employer.

    ``icims.eu`` is the sibling of ``icims.com`` — the same list bug as
    Teamtailor, found the same way. Jobgether and adeccoapps are job platforms:
    the employer is their client, never them.
    """
    for addr in (
        "noreply@talent.icims.eu",
        "noreply@match.jobgether.com",
        "noreply@info.adeccoapps.com",
    ):
        assert company_from_sender(addr) == "", addr


def test_an_employer_writing_from_its_own_domain_still_works() -> None:
    assert company_from_sender("noreply@experis.it") == "experis"
    assert company_from_sender("hr@loacker.com") == "loacker"
