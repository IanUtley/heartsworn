"""Editor Play defaults to the champion played most recently."""

import sqlite3

from tests.test_db import fresh_database

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
