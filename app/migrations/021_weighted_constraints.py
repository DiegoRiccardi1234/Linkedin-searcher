"""Re-file the two negotiable constraints as ceilings instead of hard blocks.

Years of experience and degree level used to cap an offer to 3 and hide it
behind the "applicable only" filter, exactly like a location the candidate
cannot reach. That was too much for requirements a junior is routinely told to
apply for anyway, and too little for the postings the detectors missed: EY's
"Junior Consultant Technology Risk" asked for a master's and sat at 9/10 with no
flag at all, because a "Fortemente gradita" in the *next* bullet was read as
being about the degree.

Both sides of that are stored data, and neither fixes itself:

* an offer whose only blockers were the two weighted ones is no longer capped at
  3 — but the number under that cap was never recorded, so it is NOT invented
  here. The offer is marked unevaluated, which puts it in front of the re-score
  path and into the "unscored" bulk scope. Same reasoning as migration 020;
* an offer that breaks a weighted constraint the corrected rules now see keeps
  its score, lowered to the ceiling. Nothing is invented in this direction
  either: a real model score is being lowered, which is all a ceiling ever does.

The pass itself lives in ``_shared`` because migration 024 needs the same one:
correcting a detector leaves every stored flag saying what the OLD detector saw.
"""

from __future__ import annotations

import sqlite3

VERSION = 21
DESCRIPTION = "experience and degree lower a ceiling instead of hiding the offer"


def upgrade(conn: sqlite3.Connection) -> None:
    from ._shared import reapply_weighted_constraints

    # ``release_old_caps`` is what makes this migration the one-off it is: it is
    # the pass that frees the offers pinned at 3 by the two blocks being retired.
    reapply_weighted_constraints(conn, release_old_caps=True)
