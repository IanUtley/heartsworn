"""The store listing for card sets: which Records card packs the store sells, and for how much.

A set's packs are the ``InventoryCardPack`` records Records gives it (its standard
booster, its ``FullSet`` pack that opens 4 of every card, its Primal pack, ...). Records
owns each pack's GUID, set and type; the editor writes only the store's view of a pack
(name, price, currency, tab) to ``campaign/campaign_data/store_listings.json``. The server
applies that file on start (``static._seed_store_listings``), so a listed pack appears in
the store and opens with the contents its type gives it.

A custom set has no Records packs. The editor creates its packs (a booster and a Full
Pack) as InventoryCardPack records in ``custom_sets/InventoryCardPacks.json``, each with
a new pack artwork GUID and store artwork GUID. The client loads them from the set's
.incdata, and the server seeds the set's finished cards (``static._seed_custom_cards``).
Records is never written.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

import art_upload  # noqa: E402

from paths import RECORDS  # noqa: E402  (also sets sys.path)
import campaign_data  # noqa: E402
import cards  # noqa: E402

CURRENCIES = ("Gold", "Platinum")
STORE_TABS = ("ShopBoosterTab", "special")
PACK_TYPE_LABEL = {"BoosterPack": "Booster", "FullSet": "Full Pack", "PrimalPack": "Primal Pack"}
CUSTOM_PACK_TYPES = ("BoosterPack", "FullSet")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
_LOCK = threading.RLock()   # save and create hold it while reading the Records packs
_PACKS: list[dict[str, str]] | None = None


def _records_packs() -> list[dict[str, str]]:
    """Every Records card pack (read once; Records does not change while the editor runs)."""
    global _PACKS
    with _LOCK:
        if _PACKS is None:
            from AssetExtraction.gamedata_seed import extract_card_packs
            _PACKS = [pack for pack in extract_card_packs() if pack["set_guid"] != ZERO_GUID]
        return _PACKS


def _records_pack(pack_guid: str) -> dict[str, str] | None:
    return next((pack for pack in _records_packs() if pack["pack_guid"] == pack_guid), None)


# Pack and store artwork: the client shows each as a square texture named by its file, found
# under the game's Data folder (custom_sets/<folder>/<GUID>.jpg). The editor keeps one
# 512 x 512 JPEG per GUID; an upload of another size is scaled to that.
ART_FOLDERS = {"pack": "pack_art", "store": "store_art"}
ART_PIXELS = 512
ART_MIN_PIXELS = 256
ART_MAX_PIXELS = 4096


def art_url(pack_guid: str, kind: str) -> str:
    """The URL of a pack's art (``/custom/...``), or "" when no file has been saved."""
    path = cards.CUSTOM_DIR / ART_FOLDERS[kind] / f"{pack_guid}.jpg"
    return f"/custom/{ART_FOLDERS[kind]}/{pack_guid}.jpg" if path.is_file() else ""


def save_custom_art(pack_guid: Any, kind: Any, image: Any) -> dict[str, Any]:
    """Save a pack's artwork (``pack``) or store artwork (``store``) from a data URL or base64
    image. The image must be square and between 256 and 4096 pixels on a side."""
    guid = str(pack_guid or "").strip().lower()
    if kind not in ART_FOLDERS:
        raise ValueError("art is for a pack or the store")
    if _custom_pack(guid) is None:
        raise ValueError("art is saved for a custom set's pack")
    picture = art_upload.decode_square(image, minimum=ART_MIN_PIXELS, maximum=ART_MAX_PIXELS)
    width, height = picture.size
    picture = picture.convert("RGB")
    if width != ART_PIXELS:
        picture = picture.resize((ART_PIXELS, ART_PIXELS), Image.LANCZOS)
    folder = cards.CUSTOM_DIR / ART_FOLDERS[kind]
    with _LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{guid}.jpg"
        temporary = target.with_name(target.name + ".tmp")
        picture.save(temporary, format="JPEG", quality=92)
        os.replace(temporary, target)
    return {"pack_guid": guid, "kind": kind, "url": art_url(guid, kind), "size": ART_PIXELS,
            "original": [width, height]}


def art_reference() -> list[dict[str, str]]:
    """Existing booster pack icons from the client (the editor's generated reference set)."""
    folder = Path(__file__).resolve().parent / "generated" / "pack_art_reference"
    if not folder.is_dir():
        return []
    return [{"name": path.stem, "url": f"/generated/pack_art_reference/{path.name}"}
            for path in sorted(folder.glob("*.png"))]


def _listed_by_guid() -> dict[str, dict[str, Any]]:
    return {row["pack_guid"]: row for row in campaign_data.load_store_listings()}


def _custom_pack(pack_guid: str) -> dict[str, Any] | None:
    return next((record for record in campaign_data.load_custom_packs()
                 if str(record["m_Id"]["m_Guid"]).lower() == pack_guid), None)


def _packs_of(set_guid: str) -> list[dict[str, str]]:
    """The set's packs as ``{pack_guid, pack_type, name, source}``: Records packs, then custom."""
    found = [{"pack_guid": pack["pack_guid"], "pack_type": pack["pack_type"], "name": pack["name"],
              "source": "records"} for pack in _records_packs() if pack["set_guid"] == set_guid]
    found.extend(
        {
            "pack_guid": str(record["m_Id"]["m_Guid"]).lower(),
            "pack_type": record["m_CardPackType"],
            "name": record["m_Name"],
            "source": "custom",
        }
        for record in campaign_data.load_custom_packs()
        if str(record["m_SetId"]["m_Guid"]).lower() == set_guid
    )
    return found


def store_listing(set_guid: str) -> dict[str, Any]:
    """The set's packs, each with its listing (``None`` when it is not sold) and, for a
    custom pack, its art GUIDs."""
    target = cards._set_guid(set_guid)
    entry = next((e for e in cards.card_sets() if e["guid"] == target), None)
    if entry is None:
        raise LookupError(f"no such card set: {set_guid}")
    listed = _listed_by_guid()
    packs = []
    for pack in _packs_of(target):
        custom = _custom_pack(pack["pack_guid"]) if pack["source"] == "custom" else None
        packs.append({
            "pack_guid": pack["pack_guid"], "pack_type": pack["pack_type"],
            "type_label": PACK_TYPE_LABEL.get(pack["pack_type"], pack["pack_type"]),
            "records_name": pack["name"], "source": pack["source"], "listing": listed.get(pack["pack_guid"]),
            "pack_art_guid": (custom or {}).get("m_PackArtGuid", ""),
            "store_art_guid": (custom or {}).get("m_StoreArtGuid", ""),
            "pack_art_url": art_url(pack["pack_guid"], "pack") if custom else "",
            "store_art_url": art_url(pack["pack_guid"], "store") if custom else ""})
    return {"set_guid": target, "set_name": entry["name"], "source": entry["source"],
            "packs": packs, "currencies": list(CURRENCIES), "store_tabs": list(STORE_TABS)}


def create_custom_pack(set_guid: Any, pack_type: Any) -> dict[str, Any]:
    """Add a booster or Full Pack to a custom set, with new pack and store artwork GUIDs.
    Returns the set's listing."""
    target = cards._set_guid(set_guid)
    entry = next((e for e in cards.card_sets() if e["guid"] == target), None)
    if entry is None or entry["source"] != "custom":
        raise ValueError("packs are created for a custom set")
    if pack_type not in CUSTOM_PACK_TYPES:
        raise ValueError(f"pack type must be one of {', '.join(CUSTOM_PACK_TYPES)}")
    label = PACK_TYPE_LABEL[pack_type]
    with _LOCK:
        records = campaign_data.load_custom_packs()
        records.append({
            "_t": "Reckoning.Game.InventoryCardPack",
            "m_Id": {"m_Guid": str(uuid.uuid4())},
            "m_Name": f"{entry['name']} {label}",
            "m_Description": "",
            "m_BasePrice": 0,
            "m_Type": "CardPack",
            "m_SetId": {"m_Guid": target},
            "m_CardPackType": pack_type,
            "m_AvailableForPurchase": 1,
            "m_IsPvE": 0,
            "m_PackArtGuid": str(uuid.uuid4()),
            "m_StoreArtGuid": str(uuid.uuid4()),
        })
        campaign_data.write_custom_packs(records)
    return store_listing(target)


def _listing_row(pack: dict[str, Any], records: dict[str, str]) -> dict[str, Any]:
    name = str(pack.get("name") or "").strip() or records["name"]
    if not 0 < len(name) <= 80:
        raise ValueError(f"name for {records['pack_guid']} must be 1 to 80 characters")
    try:
        price = int(pack.get("price"))
    except (TypeError, ValueError):
        raise ValueError(f"price for {name} must be a whole number") from None
    if not 0 <= price <= 1_000_000:
        raise ValueError(f"price for {name} must be between 0 and 1,000,000")
    currency = str(pack.get("currency") or "Gold")
    if currency not in CURRENCIES:
        raise ValueError(f"currency for {name} must be one of {', '.join(CURRENCIES)}")
    tab = str(pack.get("store_tab") or STORE_TABS[0])
    if tab not in STORE_TABS:
        raise ValueError(f"store tab for {name} must be one of {', '.join(STORE_TABS)}")
    return {"pack_guid": records["pack_guid"], "set_guid": records["set_guid"],
            "pack_type": records["pack_type"], "name": name,
            "short_desc": str(pack.get("short_desc") or "")[:200],
            "price": price, "currency": currency, "store_tab": tab}


def save_store_listing(set_guid: str, packs: Any) -> dict[str, Any]:
    """Replace the set's listing with ``packs``: each ``{pack_guid, listed, name, short_desc,
    price, currency, store_tab}``. Packs of other sets keep their listing."""
    target = cards._set_guid(set_guid)
    entry = next((e for e in cards.card_sets() if e["guid"] == target), None)
    if entry is None:
        raise LookupError(f"no such card set: {set_guid}")
    if not isinstance(packs, list):
        raise ValueError("packs must be a list")
    with _LOCK:
        kept = [row for row in campaign_data.load_store_listings() if row.get("set_guid") != target]
        for pack in packs:
            if not isinstance(pack, dict) or not pack.get("listed"):
                continue
            pack_guid = str(pack.get("pack_guid") or "").lower()
            if pack_guid in {p["pack_guid"] for p in _packs_of(target)}:
                records = _records_pack(pack_guid) or _custom_listing_pack(pack_guid)
            else:
                records = None
            if records is None:
                raise ValueError(f"{pack.get('pack_guid')} is not a card pack of {entry['name']}")
            kept.append(_listing_row(pack, records))
        campaign_data.write_store_listings(kept)
    return store_listing(target)


def _custom_listing_pack(pack_guid: str) -> dict[str, str]:
    record = _custom_pack(pack_guid)
    return {"pack_guid": pack_guid, "set_guid": str(record["m_SetId"]["m_Guid"]).lower(),
            "pack_type": record["m_CardPackType"], "name": record["m_Name"]}
