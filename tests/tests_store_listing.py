"""The editor's store listing reaches the store: a listed Records card pack gets its
store_items row and its pack_set_map row, and unlisting it removes the store row."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign")]

import campaign_data  # noqa: E402
import static  # noqa: E402

FULL_SET = "5e338ab1-da47-41ce-b980-4020f1b5b4fc"   # Records "Shards of Fate Full Set"
SET_1 = "0382f729-7710-432b-b761-13677982dcd2"


def test_listed_pack_reaches_the_store_and_unlisting_removes_it():
    campaign_data.STORE_LISTINGS_PATH = Path(tempfile.mkdtemp()) / "store_listings.json"
    db = sqlite3.connect(":memory:")
    for statement in static.DDL:
        db.execute(statement)
    listing = {"pack_guid": FULL_SET, "set_guid": SET_1, "pack_type": "FullSet", "name": "Set 1 Full Pack",
               "short_desc": "4 of every card", "price": 900, "currency": "Platinum", "store_tab": "ShopBoosterTab"}
    campaign_data.write_store_listings([listing])
    static._seed_store_listings(db)

    row = db.execute("SELECT name, price, currency FROM store_items WHERE template_guid=?", (FULL_SET,)).fetchone()
    assert row == ("Set 1 Full Pack", 900, "Platinum")
    assert db.execute("SELECT set_guid, is_full_set FROM pack_set_map WHERE pack_guid=?",
                      (FULL_SET,)).fetchone() == (SET_1, 1)

    campaign_data.write_store_listings([])
    static._seed_store_listings(db)
    assert db.execute("SELECT COUNT(*) FROM store_items WHERE template_guid=?", (FULL_SET,)).fetchone()[0] == 0
