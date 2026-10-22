"""Fill fra_encounters from the client's gamedata instead of hex-private/Records.

The populator (AssetExtraction/populate_fra_encounters.py) reads DeckTemplate and
ChampionTemplate JSONL. This adapter writes the same records from the gamedata
sections into that layout, then calls the populator's own extract/apply code.
"""
import json
import os
import sys
from pathlib import Path

REPO = os.environ.get("HEX_REPO", "/repo")
sys.path[:0] = [f"{REPO}/AssetExtraction", REPO]
import gamedata_seed as gs  # noqa: E402
import populate_fra_encounters as pf  # noqa: E402


def write_records(gamedata_path, records_dir="/data/records"):
    """Write every Records section the engine reads, in extract_records.py's format.

    Each line is the raw record text stored as a JSON string, which is what
    ``gamedata.records.deserialize_line`` decodes. Pyodide's file system does not
    survive a page load, so this runs on every open, not only on a fresh build.
    """
    text = gs.load_text(gamedata_path)
    out = Path(records_dir)
    out.mkdir(parents=True, exist_ok=True)
    for section in gs.RECORD_SECTIONS:
        records, errors = gs.records(text, section)
        with (out / f"{section}.jsonl").open("w", encoding="utf-8") as handle:
            for raw, _value in records:
                handle.write(json.dumps(raw) + "\n")
        print(f"{section}: {len(records)} records, {errors} unparsed")
    return out


def populate(gamedata_path, db_path, records_dir="/data/records"):
    out = write_records(gamedata_path, records_dir)
    encounter_rows, card_rows, unresolved = pf.extract_rows(out, {})
    encounters, cards = pf.apply_rows(Path(db_path), encounter_rows, card_rows)
    print(f"fra_encounters={encounters} encounter_deck_cards={cards} unresolved={len(unresolved)}")
    return encounters, cards
