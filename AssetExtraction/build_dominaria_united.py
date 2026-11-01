"""Build the Dominaria United card set as a client-shaped JSON file with Scryfall art.

Fetches every Dominaria United printing from Scryfall (set code "dmu"), converts
each card into the field names used by hex-private/Records/CardTemplate, and
writes:

    custom_sets/CardSetTemplate.json          (one Dominaria United set record)
    custom_sets/CardTemplates.json            (one CardTemplate record per card)
    custom_sets/dominaria_united/art/<id>.jpg (Scryfall art_crop, one per card)

Cost conversion (Magic mana -> Hex thresholds + resource cost):
    {B} -> Blood threshold     {G} -> Wild threshold     {W} -> Diamond threshold
    {U} -> Sapphire threshold  {R} -> Ruby threshold
    {n} and {C} -> m_ResourceCost only (generic); {X} -> m_VariableCost
    Hybrid {G/W/P} -> one Wild threshold (first colour listed).
    m_ResourceCost = generic + one per coloured symbol, so {B}{B}{G} gives
    Blood x2 + Wild x1 with cost 3.

Card text is copied verbatim as m_GameText. The abilities are NOT linked:
m_CardAbilities is left empty, so the cards have stats, costs and art but no
authored behaviour yet.

Rerunning merges into the two files instead of overwriting them. Records are
matched by m_Id GUID. A converted card keeps the editor-owned fields it already
has (EDITOR_OWNED) and has every other field refreshed from Scryfall. Records the
converter does not produce, such as cards made in the campaign editor, are left
as they are.

Usage: python3 AssetExtraction/build_dominaria_united.py
"""
import json
import re
import time
import urllib.request
import uuid
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "custom_sets" / "dominaria_united"
ART_DIR = OUT_DIR / "art"
# Custom records live beside the Records files, never inside them.
SET_FILE = ROOT / "custom_sets" / "CardSetTemplate.json"
CARD_FILE = ROOT / "custom_sets" / "CardTemplates.json"
ART_PREFIX = "custom_sets/dominaria_united/art"

SEARCH_URL = "https://api.scryfall.com/cards/search?q=set%3Admu&unique=cards&order=collector"
HEADERS = {"User-Agent": "HexPrivateServer/0.1", "Accept": "application/json"}
# Scryfall asks clients to wait 50-100 ms between requests.
SCRYFALL_DELAY = 0.15

SET_NAME = "Dominaria United"
SET_BUNDLE = "DMU"
# Sits after Set 9 in release order.
SET_NUMBER = 10
# Stable GUIDs: the same card always gets the same m_Id across rebuilds.
ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "hex-private:dominaria-united")
SET_GUID = str(uuid.uuid5(ID_NAMESPACE, SET_NAME))

COLOR_FLAGS = OrderedDict([
    ("B", "Blood"),
    ("R", "Ruby"),
    ("U", "Sapphire"),
    ("W", "Diamond"),
    ("G", "Wild"),
])

RARITY = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "mythic": "Legendary",
}


def fetch_cards():
    """Return every Dominaria United printing from Scryfall, following pagination."""
    cards = []
    url = SEARCH_URL
    while url:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = json.load(resp)
        cards.extend(page["data"])
        url = page.get("next_page") if page.get("has_more") else None
        time.sleep(SCRYFALL_DELAY)
    return cards


def parse_cost(mana_cost):
    """Split a Scryfall mana cost into (generic, [colour letters], has_x)."""
    generic = 0
    colours = []
    has_x = False
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost or ""):
        if symbol == "X":
            has_x = True
        elif symbol.isdigit():
            generic += int(symbol)
        elif symbol == "C":
            generic += 1
        elif "/" in symbol:
            # Hybrid or Phyrexian: take the first colour we can map.
            first = next(part for part in symbol.split("/") if part in COLOR_FLAGS)
            colours.append(first)
        elif symbol in COLOR_FLAGS:
            colours.append(symbol)
        else:
            raise ValueError(f"Unmapped mana symbol {{{symbol}}} in {mana_cost}")
    return generic, colours, has_x


def thresholds_for(colours):
    """One threshold entry per colour, counting repeats, or None when no colour."""
    if not colours:
        return None
    counts = OrderedDict((flag, 0) for flag in COLOR_FLAGS.values())
    for letter in colours:
        counts[COLOR_FLAGS[letter]] += 1
    return [
        {"m_ColorFlags": flag, "m_ThresholdColorRequirement": count}
        for flag, count in counts.items() if count
    ]


def card_type(type_line):
    """Map a Scryfall type line onto Hex CardTemplate m_CardType values."""
    main = type_line.split("—")[0]
    if "Land" in main:
        return "Resource"
    if "Artifact" in main and "Creature" in main:
        return "Troop|Artifact"
    if "Creature" in main:
        return "Troop"
    if "Artifact" in main:
        return "Artifact"
    if "Instant" in main:
        return "QuickAction"
    if "Sorcery" in main:
        return "BasicAction"
    # Enchantments, Sagas and Planeswalkers have no direct Hex type; they are
    # persistent cards in play, so they map to Constant.
    return "Constant"


def stat(value):
    """Return (base, variable) for a power/toughness string such as '2' or '*'."""
    if value is None:
        return 0, 0
    try:
        return int(value), 0
    except ValueError:
        return 0, 1


def land_production(oracle_text):
    """Return (colour flags, chooses_on_play, enters_tapped) for a land's text.

    Colours come from the tap-for-mana lines only ({C} is colourless and is
    left out). A land with more than one colour, or "any color", asks its
    controller to pick one when it is played. enters_tapped is "always",
    "unless" (conditional) or "never".
    """
    text = oracle_text or ""
    colours = []
    for line in re.findall(r"\{T\}: Add ([^.\n]*)", text):
        if "any color" in line:
            colours = list(COLOR_FLAGS)
            break
        for letter in re.findall(r"\{([WUBRG])\}", line):
            if letter not in colours:
                colours.append(letter)
    if "enters tapped unless" in text:
        enters_tapped = "unless"
    elif "enters tapped" in text:
        enters_tapped = "always"
    else:
        enters_tapped = "never"
    flags = [COLOR_FLAGS[letter] for letter in colours]
    return flags, len(flags) > 1, enters_tapped


def convert_card(card):
    scryfall_id = card["id"]
    type_line = card["type_line"]
    generic, colours, has_x = parse_cost(card.get("mana_cost", ""))
    is_land = "Land" in type_line
    land_fields = {}
    if is_land:
        # Hex lands make mana through their abilities, which are not linked yet.
        # The produced colours are kept as data for when those abilities exist.
        generic, colours, has_x = 0, [], False
        flags, chooses, enters_tapped = land_production(card.get("oracle_text", ""))
        land_fields = OrderedDict([
            ("m_LandProduces", flags),
            ("m_ChoosesColourOnPlay", 1 if chooses else 0),
            ("m_EntersTapped", enters_tapped),
        ])
    attack, variable_attack = stat(card.get("power"))
    defense, variable_defense = stat(card.get("toughness"))
    subtype = type_line.split("—", 1)[1].strip() if "—" in type_line else ""
    collector = card["collector_number"]

    record = OrderedDict([
        ("m_Id", {"m_Guid": str(uuid.uuid5(ID_NAMESPACE, scryfall_id))}),
        ("m_SetId", {"m_Guid": SET_GUID}),
        ("m_Name", card["name"]),
        ("m_CardNumber", int(collector) if collector.isdigit() else 0),
        ("m_CardType", card_type(type_line)),
        ("m_CardSubtype", subtype),
        ("m_CardRarity", "Land" if is_land else RARITY[card["rarity"]]),
        ("m_ResourceCost", generic + len(colours)),
        ("m_VariableCost", 1 if has_x else 0),
        ("m_Threshold", thresholds_for(colours)),
        ("m_BaseAttackValue", attack),
        ("m_BaseDefenseValue", defense),
        ("m_VariableAttack", variable_attack),
        ("m_VariableDefense", variable_defense),
        ("m_Unique", 1 if "Legendary" in type_line else 0),
        ("m_Faction", "None"),
        ("m_GameText", card.get("oracle_text", "")),
        ("m_FlavorText", card.get("flavor_text", "")),
        ("m_ArtistName", card.get("artist", "")),
        ("m_CardAbilities", []),
        ("m_CardImagePath", f"{ART_PREFIX}/{scryfall_id}.jpg"),
        ("m_ManaCost", card.get("mana_cost", "")),
        ("m_ScryfallId", scryfall_id),
        ("m_ScryfallCollectorNumber", collector),
    ])
    record.update(land_fields)
    return record


def download_art(card):
    """Save the art crop for one card unless it is already on disk."""
    target = ART_DIR / f"{card['id']}.jpg"
    if target.exists():
        return
    art_url = card["image_uris"]["art_crop"]
    req = urllib.request.Request(art_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        target.write_bytes(resp.read())
    time.sleep(SCRYFALL_DELAY)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)

    scryfall_cards = fetch_cards()
    cards = [convert_card(c) for c in scryfall_cards]
    for scryfall_card in scryfall_cards:
        download_art(scryfall_card)

    set_record = OrderedDict([
        ("m_Id", {"m_Guid": SET_GUID}),
        ("m_Name", SET_NAME),
        ("m_Tags", ""),
        ("m_SetNo", SET_NUMBER),
        ("m_BundleName", SET_BUNDLE),
        ("m_ReleaseDate", scryfall_cards[0].get("released_at", "")),
    ])
    sets, set_counts = merge_records(read_records(SET_FILE), [set_record])
    merged, card_counts = merge_records(read_records(CARD_FILE), cards, owned=EDITOR_OWNED)
    write_json(SET_FILE, sets)
    write_json(CARD_FILE, merged)
    print(f"Sets {SET_FILE}: {set_counts}")
    print(f"Cards {CARD_FILE}: {card_counts}")


# Fields the campaign editor edits on a custom card (campaign_editor/cards.py
# _apply_to_custom), plus m_CardAbilities, which holds authored behaviour. A rerun
# keeps these from the file when the file already has them.
EDITOR_OWNED = ("m_Name", "m_SetId", "m_CardRarity", "m_ResourceCost", "m_BaseAttackValue",
                "m_BaseDefenseValue", "m_CardType", "m_CardSubtype", "m_Threshold",
                "m_VariableCost", "m_FlavorText", "m_CardAbilities")


def read_records(path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def guid_of(record):
    return str(record["m_Id"]["m_Guid"]).lower()


def merge_records(existing, fresh, owned=()):
    """Merge converted records into the existing ones, by GUID.

    Existing order is kept, and records the converter does not produce are left
    as they are. For a converted record, a field in ``owned`` keeps the file's value
    when the file has one; every other field takes the converted value. New
    records are appended in conversion order. Returns (records, counts).
    """
    fresh_by_guid = OrderedDict((guid_of(record), record) for record in fresh)
    merged, seen = [], set()
    counts = {"updated": 0, "added": 0, "kept": 0}
    for record in existing:
        guid = guid_of(record)
        if guid not in fresh_by_guid:
            merged.append(record)
            counts["kept"] += 1
            continue
        seen.add(guid)
        updated = dict(record)
        for key, value in fresh_by_guid[guid].items():
            if key in owned and key in record:
                continue
            updated[key] = value
        merged.append(updated)
        counts["updated"] += 1
    for guid, record in fresh_by_guid.items():
        if guid not in seen:
            merged.append(record)
            counts["added"] += 1
    return merged, counts


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
