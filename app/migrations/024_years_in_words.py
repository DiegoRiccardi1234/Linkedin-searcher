"""Re-read the archive with a years detector that can count in words.

``_estimate_experience_band`` only ever matched digits. Italian postings write
the number out as often as not, and the detector was blind to every one of them:
measured on 348 real offers, fifteen spell it out and **six state a genuine
requirement** — "Almeno quattro anni di esperienza nel ruolo di Project Manager"
among them, on a posting sitting in the shortlist at 6/10 with no warning at all.
The same pass also stopped reading "al termine dei due anni otterrai il diploma"
as a demand, which is the length of an apprenticeship, not a prerequisite.

A corrected detector does nothing for what is already stored: a flag is a
snapshot of what the old rules saw. This re-runs the deterministic checks — the
very same ones the next scan will run — over every analysed offer.

No model is called and no score is invented: the pass adds a flag, or lowers a
real score to a ceiling, and nothing else. Measured before writing it, on the
real archive: **14 offers change band, 6 cross the blocking threshold, 0 stop
being flagged.**

It deliberately does NOT pass ``release_old_caps``. That is 021's one-off job,
and trying it a second time on an archive 021 already cleaned cannot tell a 3
that was a cap from a 3 the model actually gave — the trial run on a copy of the
real archive wiped **40 legitimate verdicts** before that flag existed. Which is
also the reason this project runs every data-rewriting migration on a copy first.
"""

from __future__ import annotations

import sqlite3

VERSION = 24
DESCRIPTION = "re-apply the constraint checks now that years written in words are read"


def upgrade(conn: sqlite3.Connection) -> None:
    from ._shared import reapply_weighted_constraints

    reapply_weighted_constraints(conn)
