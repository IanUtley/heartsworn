"""Editor Play defaults to the champion played most recently."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database  # noqa: E402

SRC = fresh_database()


def make_db():
    db = sqlite3.connect(":memory:")
    sqlite3.connect(SRC).backup(db)
    return db


def test_playtest_champion_is_the_last_played_not_the_newest():
    from profile_db import (db_last_played_champion_for_user,
                            db_mark_champion_played)
    db = make_db()
    for cid, deck in ((1, 10), (2, 20), (3, 30)):
        db.execute("INSERT INTO champions (id, user_id, champion_name, "
                   "last_deck_id) VALUES (?, 900, 'c', ?)", (cid, deck))
    db.commit()
    # Never played: the newest champion (highest id) wins, as before.
    assert tuple(db_last_played_champion_for_user(900, conn=db)) == (3, 30)
    db_mark_champion_played(1, conn=db)
    assert tuple(db_last_played_champion_for_user(900, conn=db)) == (1, 10)
    db_mark_champion_played(3, conn=db)
    assert tuple(db_last_played_champion_for_user(900, conn=db)) == (3, 30)


if __name__ == "__main__":
    # Written in pytest style: run through pytest so each test executes and failures
    # set the exit status. A missing pytest fails loudly.
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
