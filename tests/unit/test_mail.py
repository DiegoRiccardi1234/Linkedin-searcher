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

from app.mail.matcher import MailHeader, PendingJob, classify, is_known_sender
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


def test_nothing_is_fetched_when_there_is_nothing_to_wait_for(tmp_path: Path) -> None:
    """The usual state of this feature is "no applications in flight", and in
    that state it must not touch the network at all."""
    from app.db import Database
    from app.mail import config as mail_config
    from app.services.scan_control import ScanControl

    db = Database(tmp_path / "d.db")
    try:
        (tmp_path / "data").mkdir(exist_ok=True)
        mail_config.save_account(
            db, tmp_path / "data", address="me@libero.it", auth="password",
            host="imapmail.libero.it", secret="pw",
        )
        db.set_preference(mail_config.PREF_LAST_OK, "99999999999")  # keep-warm not due

        def _explode(_account: object) -> object:
            raise AssertionError("the mailbox was opened with nothing pending")

        watcher = MailWatcher(db, tmp_path / "data", ScanControl(), imap_factory=_explode)
        assert watcher.run_once()["status"] == "idle"
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
