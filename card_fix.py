"""Post-extraction data fixes for card abilities.

Apply fixes to the DB after card templates and ability metadata have been
seeded from gamedata.  Each fix is a named function with a comment explaining
WHY the data value is overridden.

Called from static.ensure_schema() after all seeds are inserted.

No fixes are active.  The former TurnStartedEvent no-chain overrides (The
Ancestors' Chosen, then every TurnStartedEvent trigger) were removed: the
client-matched untargeted-trigger rule now resolves these triggers without a
priority window, and the authored gamedata value (m_IgnoresChain=0) is kept.
"""


def apply_fixes(db):
    """Run all card-ability data fixes against the given DB connection.

    Fixes are idempotent — they check the current state before mutating.
    Call this after static.ensure_schema() has seeded the tables.
    """
    # ── Fixes ──────────────────────────────────────────────────────────
    # Order: add new fixes at the bottom.  Each fix has its own commit.

    db.commit()
