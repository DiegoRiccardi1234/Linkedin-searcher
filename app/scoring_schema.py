"""Version of the AI analysis schema, shared by the scorer and the database.

Lives in its own module because both sides need it and the import can only go
one way: :mod:`app.services.scanner_service` (which produces analyses) already
imports :mod:`app.db` (which stores them), so the constant cannot live in
either without a cycle.

Why a version at all: the scan loop must decide *skip vs re-score* for a job it
has already seen. That decision used to be made by looking for a marker key
inside the JSON blob (``titolo_studio_richiesto``, then
``eleggibilita_geografica``) — which broke twice, because the normaliser injects
those keys into EVERY analysis, heuristic fallbacks included. A job scored by
pure keyword matching therefore looked "already analysed" and was frozen at that
score forever.

Now an analysis carries an explicit version, persisted in the ``jobs.analysis_v``
column, and only analyses that a model actually produced carry one at all
(``_heuristic_analysis`` marks its output as heuristic and gets none). So:

- heuristic / fallback analyses  -> ``analysis_v`` NULL -> re-scored next scan;
- analyses older than the current version -> re-scored once, then self-heal;
- bumping :data:`CURRENT_ANALYSIS_VERSION` is the intended way to force a
  one-off mass re-score after a change to the scoring schema or rules.
"""

from __future__ import annotations

#: Bump when the scoring schema or the scoring rules change in a way that makes
#: previously stored analyses untrustworthy. Every stored analysis below this
#: number is re-scored once, the next time its job shows up in a scan.
#:
#: 1 = pre-versioning analyses (marker-key era, v1.7.6 and earlier).
#: 2 = v1.7.7: slimmed/reordered schema, explicit scoring rubric, salary axis
#:     that is honestly null when nothing is known about pay.
CURRENT_ANALYSIS_VERSION = 2

#: Key holding :data:`CURRENT_ANALYSIS_VERSION` inside a stored analysis dict.
ANALYSIS_VERSION_KEY = "scoring_v"

#: Value of ``fonte_analisi`` marking an analysis no model ever saw. Such an
#: analysis is a legitimate result to show the user (honest capped estimate) but
#: never a reason to skip re-scoring the job later.
HEURISTIC_SOURCE = "euristica"

#: Key carrying :data:`HEURISTIC_SOURCE`.
ANALYSIS_SOURCE_KEY = "fonte_analisi"
