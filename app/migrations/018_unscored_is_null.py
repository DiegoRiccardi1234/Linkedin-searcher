"""Strip the scores nobody ever computed.

Every failure path used to produce a number anyway, by counting words the
posting shared with the CV. Those numbers are indistinguishable from a model's
verdict once stored, and they sort among the real ones: on the archive this
migration was written against, 7 of the 11 highest-scoring off-target offers had
never been read by anything — a PAYROLL SPECIALIST at 6/10, a DIGITAL
COMMUNICATION SPECIALIST at 8/10.

The scan self-heals such jobs by re-scoring them, but only when the same posting
turns up again — which, for an expired ad, never happens. So the cleanup has to
run once, here.

Two rules:

- rows that were never analysed at all keep ``punteggio_ai = 0`` from the column
  default, which is itself a lie: they become NULL;
- heuristic rows (``analysis_v IS NULL`` with a stored analysis) lose their
  score, advice, summary and match axes — EXCEPT when the analysis carries a
  hard blocker, whose 3/10 was computed by the app from the posting itself and
  is a genuine verdict.

``analysis_v`` stays NULL throughout, so these jobs are still re-scored by the
next scan (or on demand from the detail panel).
"""

from __future__ import annotations

import json
import sqlite3

VERSION = 18
DESCRIPTION = "unscored jobs carry NULL, not an invented score"

#: Flags whose cap is a real verdict: computed deterministically, not guessed.
_BLOCKING = {"geo_non_ue", "voto_minimo"}

_AXES = (
    "skills_match",
    "seniority_match",
    "remote_match",
    "salary_match",
    "contract_match",
)


def upgrade(conn: sqlite3.Connection) -> None:
    # A pre-baseline DB may carry a ``jobs`` table from before these columns
    # existed; the earlier migrations add them, but this one must not assume it.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not {"punteggio_ai", "consiglio", "analysis_json", "analysis_v"} <= cols:
        return

    # 1. Never analysed: the DEFAULT 0 was already a claim nobody made.
    conn.execute(
        "UPDATE jobs SET punteggio_ai = NULL, consiglio = '' "
        "WHERE (analysis_json IS NULL OR analysis_json = '') AND punteggio_ai IS NOT NULL"
    )

    # 2. Keyword-scored: rewrite the blob so the UI reads it as unevaluated.
    rows = conn.execute(
        "SELECT id, analysis_json FROM jobs "
        "WHERE analysis_v IS NULL AND analysis_json IS NOT NULL AND analysis_json != ''"
    ).fetchall()
    for job_id, raw in rows:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        flags = [str(code) for code in (data.get("blocchi") or []) if isinstance(code, str)]
        if _BLOCKING & set(flags):
            continue  # a computed cap, not an invented score
        data["punteggio"] = None
        data["consiglio"] = ""
        # "Analisi euristica usata. Match stimato 6/10." was the sentence that
        # made the number look like a finding.
        data["riassunto"] = ""
        data["punti_forza"] = ""
        data["match_axes"] = dict.fromkeys(_AXES)
        data["fonte_analisi"] = "non_valutata"
        flags = [code for code in flags if code != "analisi_locale"]
        if "non_valutato" not in flags:
            flags.append("non_valutato")
        data["blocchi"] = flags
        conn.execute(
            "UPDATE jobs SET analysis_json = ?, punteggio_ai = NULL, consiglio = '' WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), job_id),
        )
