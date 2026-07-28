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

#: Value of ``fonte_analisi`` marking an analysis the app computed itself,
#: deterministically, without asking a model — today only the hard-blocker path
#: (outside the EU, degree grade below the stated minimum), whose score is
#: CALCULATED rather than guessed. Never a reason to skip re-scoring later.
HEURISTIC_SOURCE = "euristica"

#: Value of ``fonte_analisi`` marking an offer NOBODY judged: the provider was
#: down, the answer was unusable, or the posting carried no description worth
#: reading. Such an analysis carries no score, no advice and no match axes.
#:
#: Until v1.7.8 this case was filled with a keyword-overlap estimate — a number
#: the user could not tell apart from a model's verdict, and which put postings
#: nobody had read at the top of the list (measured: 7 of 11 false positives on
#: a real scan). An unjudged job is now shown as unjudged, and re-scored on the
#: next scan or on demand.
NOT_EVALUATED_SOURCE = "non_valutata"

#: Key carrying :data:`HEURISTIC_SOURCE`.
ANALYSIS_SOURCE_KEY = "fonte_analisi"
