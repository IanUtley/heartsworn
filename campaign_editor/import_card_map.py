#!/usr/bin/env python3
"""Write ``campaign/campaign_data/card_map.json``: the editor's stand-in for each
encounter-deck card GUID that Records never defines.

Some DeckTemplate and EncounterDeck rows name card GUIDs that no CardTemplate
record has (209 of them, none in the client data either). Until each one is
remapped in the campaign editor, it maps to the Shards of Fate stand-in. A
remap the editor already saved is kept; a GUID that Records now defines drops
out of the file.

    python3 campaign_editor/import_card_map.py            # write the file
    python3 campaign_editor/import_card_map.py --dry-run  # report only

``--dry-run`` reports whether the file would change.
"""

from __future__ import annotations

import argparse
import json

import paths  # noqa: E402,F401  (sets sys.path)
import campaign_data  # noqa: E402
from AssetExtraction.gamedata_seed import (  # noqa: E402
    configured_records_path, load_records_text, nested_guid, records)

OUT = campaign_data.CARD_MAP_PATH
# Shards of Fate (CardTemplate, Resource): the card an unmapped GUID shows as.
STAND_IN = "00e13fdf-b2c3-4fe7-a064-ce4481b24e8d"


def unresolved_card_guids() -> set[str]:
    """Card GUIDs a DeckTemplate or EncounterDeck names that Records never defines."""
    data = load_records_text(configured_records_path())
    known = {nested_guid(r, "m_Id") for _, r in records(data, "CardTemplate")[0]}
    named = set()
    for _, record in records(data, "DeckTemplate")[0]:
        for entry in record.get("m_DeckResources") or []:
            if isinstance(entry, dict):
                named.add(nested_guid(entry, "m_idTemplate"))
    for _, record in records(data, "EncounterDeck")[0]:
        for entry in record.get("m_DeckOrder") or []:
            if isinstance(entry, dict):
                named.add(nested_guid(entry, "m_Guid"))
    return {guid for guid in named if guid and guid not in known}


def build(existing: dict[str, str], unresolved: set[str]) -> dict[str, str]:
    """Each unresolved GUID keeps its saved remap, or takes the stand-in."""
    return {guid: existing.get(guid, STAND_IN) for guid in sorted(unresolved)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    existing = campaign_data.load_card_map()
    cards = build(existing, unresolved_card_guids())
    document = {"schema_version": campaign_data.SCHEMA_VERSION, "cards": cards}
    text = json.dumps(document, indent=1) + "\n"
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if current == text:
        print(f"{OUT} is up to date ({len(cards)} cards)")
        return
    remapped = sum(1 for guid, card in cards.items() if card != STAND_IN)
    print(f"{len(cards)} unresolved card GUIDs, {remapped} remapped away from the stand-in")
    if args.dry_run:
        print("would change " + str(OUT))
        return
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
