"""Apply two requirements the archive was scored without.

**The subject of the degree.** ``education_status`` compares a bachelor's
against a master's and says nothing about what the degree is *in*. Measured on
423 real descriptions, 245 of them name a subject: it is the most stated
requirement in the whole archive and the only one nothing read. So "Ti stai per
laureare o hai una laurea in Economia" counted as satisfied by a computer-science
CV, and PwC's junior auditor sat at 8/10 in a shortlist built for an IT job hunt.

**A declared salary under the declared floor.** That was flagged and nothing
else, on the reasoning that capping would punish the rare posting honest enough
to publish a figure. What it produced: a TIM internship declaring up to 9.600 EUR
against a 20.000 floor kept the model's 8/10 and led the "best for you" panel.

Both are arguable — people are hired across a subject line, and an internship can
be worth taking — so both are WEIGHTED, never blocking: the offer keeps its place
in the list and its badge, it just stops outranking one that fits. Nothing here
invents a score; the most it does is lower a real one to a ceiling.

Idempotent: ``release_old_caps`` stays off, so a second run finds the flags
already present and the scores already under the ceiling, and rewrites nothing.
"""

from __future__ import annotations

import sqlite3

from app.migrations._shared import reapply_weighted_constraints

VERSION = 27
DESCRIPTION = "campo_studio + ral_sotto_minima: due requisiti che non muovevano il punteggio"


def upgrade(conn: sqlite3.Connection) -> None:
    reapply_weighted_constraints(conn, release_old_caps=False)
