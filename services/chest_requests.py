"""Chest-spin, card-pack opening and chest reservation requests.

Registered in ``services/__init__.py``; each request type is routed to its
handler by ``data_type`` through :data:`CHEST_REQUEST_HANDLERS`.
"""

import json
import random
import re
from db import log_req
import db
from encoder import encode_datawrapper
from encoder import compress_gzip
from encoder import encode_objfmt_response
from domain.uids import SERVICE_PROFILE_UID
from db import STARDUST_TEMPLATES
from profile_db import db_inventory_item
from profile_db import db_insert_card_instance
from az1_pack import CAMPAIGN_PACK_CONFIGS
from az1_pack import generate_campaign_pack
from profile_db import db_add_card
from profile_db import db_next_card_instance_for_user
from services.chests import _spin_client_chest
from services.chests import _encode_spin_response
from profile_db import db_pack_set_info
from az1_pack import STARDUST_REPLACEMENT_RATE
from profile_db import db_consume_inventory
from services.chests import _grant_pack_inventory_rewards
from services.chests import _roll_pack_chest_rarities
from profile_db import db_create_treasure_chest
from services.chests import _open_client_chests


def handle_chest_spinning_2049(handler, target, instance, data_type, reqid, comp, session_id,
                               conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    chest_id_raw = inner_obj.get("ChestID", "0")
    # ObjFmt may decode the integer ChestID as either a Python int or
    # a string, depending on the request shape.  Normalize both forms
    # at the protocol boundary instead of calling string-only APIs.
    try:
        chest_uid = int(chest_id_raw)
    except (TypeError, ValueError):
        chest_uid = 0
    log_req(f">>> SpinWheelOfFate: ChestID={chest_uid}")

    # A spin does not open the chest: it may award prizes, re-spins, or
    # upgrade the chest's rarity, and the player opens it afterwards.
    spin = (_spin_client_chest(handler, chest_uid)
            if handler.user_profile else None)
    if spin is None:
        log_req(f"    No profile")
        resp_inner = b""
    else:
        if spin["error"]:
            log_req(f"    Spin refused: error={spin['error']}")
        reward_card_bits = spin["cards"]
        # Push reward cards and inventory changes to client
        if reward_card_bits:
            handler._send_cards_chunk(reward_card_bits)
        for template_guid, item_uid, quantity in spin["inventory_updates"]:
            handler.push_inventory_to_client(
                qty=quantity, template_guid=template_guid,
                item_id=item_uid)
        # The client refreshes PlayerProfile.Gold only from BalanceUpdate.
        if not spin["error"] and hasattr(handler, "push_balance_to_client"):
            handler.push_balance_to_client()
        resp_inner = _encode_spin_response(spin, chest_uid)

    if not resp_inner:
        resp_inner = encode_objfmt_response(
        ["Game.Client.Network.Profile.SpinWheelOfFateResponse"],
        []
    )
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceProfile.{SERVICE_PROFILE_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str, "target": target, "instance": instance,
        "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    log_req(f"    Sent SpinWheelOfFate response ({len(dw_bytes)}b)")


def handle_opencardpack_2127(handler, target, instance, data_type, reqid, comp, session_id,
                             conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    item_id = inner_obj.get("ItemId", "{}")
    open_amount = inner_obj.get("OpenAmount", 1)
    log_req(f">>> OpenCardPack: id={item_id} amount={open_amount}")
    
    # Extract template guid from client raw bytes
    # ResourceId uses DataMember field "m_Guid" (not "guid")
    pack_guid = "0382f729-7710-432b-b761-13677982dcd2"
    if isinstance(inner_bytes, bytes) and b"ItemId" in inner_bytes:
        pos = inner_bytes.find(b"ItemId")
        if pos >= 0:
            rest = inner_bytes[pos:]
            for fname in (b"m_Guid", b"guid"):
                gpos = rest.find(fname)
                if gpos >= 0:
                    rest2 = rest[gpos:]
                    parts = rest2.split(b";", 5)
                    if len(parts) >= 5 and parts[4].isdigit():
                        try:
                            guid_len = int(parts[4])
                            tail = b";".join(rest2.split(b";", 5)[:5])
                            remain = rest2[len(tail)+1:]
                            if len(remain) >= guid_len:
                                pack_guid = remain[:guid_len].decode("ascii")
                                break
                        except:
                            pass
    log_req(f">>> OpenCardPack: pack_guid={pack_guid} amount={open_amount}")

    # Map store item GUID to card set GUID — query pack_set_map table
    row = db_pack_set_info(pack_guid, conn=db._db)
    if row:
        set_guid, is_full_set, is_primal = row
    else:
        set_guid = pack_guid  # fallback: treat pack GUID as set GUID
        is_full_set = 0
        is_primal = 0
    log_req(f"    pack_guid={pack_guid} → set_guid={set_guid} full={is_full_set} primal={is_primal}")

    # Validate player owns this pack
    pack_error = None
    pack_error_code = 0
    if handler.user_profile:
        existing = db_inventory_item(
        handler.user_profile["id"], pack_guid, conn=db._db)
        if not existing or existing[1] < open_amount:
            pack_error = "NotEnoughInventory"
            pack_error_code = 5
            log_req(f"    Pack not in inventory! (have={existing[1] if existing else 0}, want={open_amount})")
    
    # Generate card instances for the pack
    card_templates = _hc._load_card_templates()
    all_cards = []
    pack_inventory_rewards = []
    pack_inventory_updates = []
    campaign_pack_config = CAMPAIGN_PACK_CONFIGS.get(pack_guid.lower())
    starter_pack = None
    if not pack_error:
        try:
            starter_pack = _hc._generate_starter_deck(
                card_templates, pack_guid)
        except ValueError as exc:
            pack_error = "CardCreationFailed"
            pack_error_code = 3
            log_req(f"    Starter deck contents unavailable: {exc}")
    if not pack_error:
        if campaign_pack_config:
            # Adventure Zone campaign packs are not normal PvP
            # boosters.  Each has two common PvP cards, one weighted
            # PvE card, and two equipment slots from its configured set
            # range.  They award no Stardust (allow_stardust is off).
            from gamedata import DEFAULT_RECORD_STORE
            for _ in range(open_amount):
                reward = generate_campaign_pack(
                card_templates, DEFAULT_RECORD_STORE, random,
                pack_config=campaign_pack_config)
                all_cards.extend(reward.cards)
                pack_inventory_rewards.extend(
                    (guid, "equipment")
                    for guid in reward.equipment_guids)
                pack_inventory_rewards.extend(
                    (STARDUST_TEMPLATES[rarity.lower()], "stardust")
                    for rarity in reward.stardust_rarities)
                log_req(
                f"    Campaign pack: {len(all_cards)} cards, "
            f"{sum(kind == 'equipment' for _, kind in pack_inventory_rewards)} "
            f"equipment, "
                f"{sum(kind == 'stardust' for _, kind in pack_inventory_rewards)} "
                f"stardust (replacement rate={STARDUST_REPLACEMENT_RATE:.0%})")
        elif starter_pack:
            for _ in range(open_amount):
                all_cards.extend(starter_pack["cards"])
            log_req(
            f"    Starter deck {starter_pack['definition']['name']}: "
            f"{len(starter_pack['cards'])} cards x {open_amount}")
        elif is_full_set:
            # Grant every PVP card from the set (4x each)
            pool = card_templates.get(set_guid, [])
            pool = _hc._full_set_pool(pool)
            for _ in range(open_amount):
                for c in pool:
                    for _ in range(4):  # 4x each card
                        all_cards.append((c[0], c[1], c[3], c[4], c[5]))
            log_req(f"    Full set: {len(pool)} unique cards x4 = {len(all_cards)} total")
        else:
            for _ in range(open_amount):
                all_cards.extend(_hc._generate_booster(card_templates, set_guid))
    
    # Track instance IDs for response encoding (fallback: 5000+)
    card_instance_ids = [5000 + i for i in range(len(all_cards))]
    new_chests = []   # (chest rarity, treasure_chests.id)
    
    # Persist generated cards to user's collection
    if handler.user_profile and not pack_error:
        for guid, _, _, _, _ in all_cards:
            db_add_card(handler.user_profile["id"], guid)
    
        # Remove the booster pack from inventory
        consumed = db_consume_inventory(
        handler.user_profile["id"], pack_guid, open_amount, conn=db._db)
        if consumed:
            pack_client_uid, new_qty = consumed
            log_req(f"    Consumed {open_amount}x pack {pack_guid} from inventory (remaining={max(0, new_qty)})")
            # Push inventory update so client pack count decrements
            if pack_client_uid:
                handler.push_inventory_to_client(qty=max(0, new_qty), template_guid=pack_guid, item_id=pack_client_uid)
    
        # Persist new cards to card_instances and get their instance IDs
        # ``db_next_card_instance_for_user`` already returns the
        # first unused instance ID.  Do not subtract one here: the
        # old calculation reused the current maximum, and the
        # INSERT OR IGNORE in ``db_insert_card_instance`` then kept
        # whatever card happened to occupy that ID.  That made the
        # first card in an opened pack appear as an unrelated card.
        next_id = db_next_card_instance_for_user(
        handler.user_profile["id"], conn=db._db)
        new_card_data = []
        for i, (guid, name, cost, atk, def_) in enumerate(all_cards):
            cid = next_id + i
            db_insert_card_instance(
            handler.user_profile["id"], cid, guid, conn=db._db)
            new_card_data.append((guid, name, cost, atk, def_, cid, 0))
            card_instance_ids[i] = cid  # Update for response encoding
        db._db.commit()
        log_req(f"    Created {len(new_card_data)} card_instances IDs {new_card_data[0][5]}-{new_card_data[-1][5]}")

        # OpenCardPackResponse.NewCardInstances is the authoritative
        # collection update.  Do not also send these same instances
        # through ProfileGenericUpdate: PlayerProfile.LoadCardsFromBits
        # adds them to the client's CardList, and the duplicate update
        # causes the client's dictionary to throw "same key already
        # exists" while opening the pack.

        # Campaign equipment and Stardust are InventoryItemData, not
        # card_instance_bits.  Persist them with stable per-player
        # inventory IDs and push the same quantity update to the
        # client.  Keeping the rewards in player_inventory also makes
        # them survive a reconnect.
        if pack_inventory_rewards:
            pack_inventory_updates = _grant_pack_inventory_rewards(
            handler, pack_inventory_rewards)
            db._db.commit()

        # Every booster pack contains one treasure chest
        # (BoosterPack_Content_Combined), and a Primal Pack a Legendary
        # one.  Campaign packs already contain their two equipment/Stardust
        # slots and starter packs are decks; neither awards a chest.
        if not (campaign_pack_config or starter_pack):
            for chest_rarity in _roll_pack_chest_rarities(
                open_amount, is_primal):
                chest_db_id = db_create_treasure_chest(
                handler.user_profile["id"], set_guid, chest_rarity,
                conn=db._db)
                new_chests.append((chest_rarity, chest_db_id))
            db._db.commit()
            log_req(f"    Generated {len(new_chests)} chests: "
                    f"{[rarity for rarity, _ in new_chests]}")
    
    # Encode OpenCardPackResponse using builder
    from objfmt_builder import ObjFmtBuilder
    b = ObjFmtBuilder("Game.Client.Network.Profile.OpenCardPackResponse")

    # NewCardInstances
    card_count = len(all_cards)
    b.begin_list("NewCardInstances",
        "System.Collections.Generic.List`1#Game.Shared.Domain.card_instance_bits", card_count)
    for i, (tguid, tname, cost, atk, def_) in enumerate(all_cards):
        b.begin_element(i, "Game.Shared.Domain.card_instance_bits", b.CARD_PROPS)
        b.card_fields(tguid, card_instance_ids[i])

    # NewGemInstances (empty)
    b.begin_list("NewGemInstances",
        "System.Collections.Generic.List`1#Game.Shared.Domain.inventory_bits", 0)

    # NewChestInstances: one chest per opened pack.  A new booster chest
    # holds one paid Wheels of Fate spin.
    from services.wheel_of_fate import PAID_SPIN
    chest_map = {"Common":0, "Uncommon":1, "Rare":2, "Legendary":3, "Primal":4}
    b.begin_list("NewChestInstances",
        "System.Collections.Generic.List`1#Game.Shared.Domain.chest_bits",
        len(new_chests))
    for index, (chest_rarity, chest_db_id) in enumerate(new_chests):
        b.begin_element(index, "Game.Shared.Domain.chest_bits", 8)
        b.chest_fields(chest_map.get(chest_rarity, 0), PAID_SPIN, set_guid,
                       9000 + chest_db_id)

    # Error + ErrorMessage
    error_val = pack_error_code
    error_msg = ("Not enough inventory" if pack_error_code == 5 else
                 "Card creation failed" if pack_error_code == 3 else "")
    b.field_enum("Error", "Game.Shared.Network.Profile.EOpenCardPackError", error_val)
    b.field_str("ErrorMessage", error_msg)

    resp_inner = b.finish(5)
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceProfile.{SERVICE_PROFILE_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str, "target": target, "instance": instance,
        "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    for template_guid, item_uid, quantity in pack_inventory_updates:
        handler.push_inventory_to_client(
            qty=quantity, template_guid=template_guid,
            item_id=item_uid)
    log_req(f"    Sent OpenCardPack response ({len(all_cards)} cards, {len(dw_bytes)}b)")


def handle_reserved_for_chests_with_a_2129(handler, target, instance, data_type, reqid, comp, session_id,
                                           conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    raw_chest_ids = inner_obj.get("ChestIds", [])
    if isinstance(raw_chest_ids, str):
        try:
            raw_chest_ids = json.loads(raw_chest_ids)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_chest_ids = re.findall(r"\d+", raw_chest_ids)
    if not isinstance(raw_chest_ids, (list, tuple)):
        raw_chest_ids = [raw_chest_ids]
    requested_ids = []
    for raw_id in raw_chest_ids:
        try:
            chest_uid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if chest_uid > 0:
            requested_ids.append(chest_uid)
    log_req(f">>> OpenChest: ids={requested_ids}")

    error_val = 0
    error_message = ""
    summary = None
    if not handler.user_profile:
        error_val = 1  # InvalidChestID
        error_message = "Invalid chest ID"
    else:
        summary = _open_client_chests(handler, requested_ids)
        if requested_ids and not summary["opened"]:
            error_val = 1
            error_message = "Invalid chest ID"

    valid_chest_ids = [uid for _t, uid, _r in summary["opened"]] \
        if summary else []
    inventory_template_ids = summary["inventory_template_ids"] \
        if summary else []
    card_template_ids = summary["card_template_ids"] if summary else []
    from objfmt_builder import ObjFmtBuilder
    b = ObjFmtBuilder("Game.Client.Network.Profile.OpenChestResponse")
    b.field_ulong_list("validChestIds", valid_chest_ids)
    b.field_resource_id_list("inventoryTemplateIDs",
                             inventory_template_ids)
    b.field_resource_id_list("cardTemplateIDs", card_template_ids)
    b.field_int("goldAcquired", 0)
    b.field_int("platinumAcquired", 0)
    b.field_enum(
        "Error", "Game.Shared.Network.Profile.EOpenChestError",
        error_val)
    b.field_str("ErrorMessage", error_message)
    resp_inner = b.finish(7)
    resp_body = compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = encode_datawrapper(
    resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = (
        f"0.0.0.0.ServiceProfile.{SERVICE_PROFILE_UID}."
        f"ServicePlayer.{handler.client_uid}.{resp_reqid}")
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str, "target": target, "instance": instance,
        "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
    }, dw_bytes)
    log_req(
    f"    Sent OpenChest response ({len(card_template_ids)} cards, "
    f"{len(dw_bytes)}b)")
    # OpenChestResponse only drives the reward display.  Collection
    # cards, equipment/Stardust rewards and the consumed chest entry
    # arrive through the normal profile events, just as they do for the
    # other pack-opening paths.
    if summary:
        if summary["cards"]:
            handler._send_cards_chunk(summary["cards"])
        for template_guid, item_uid, quantity in summary["inventory_updates"]:
            handler.push_inventory_to_client(
                qty=quantity, template_guid=template_guid,
                item_id=item_uid)
        for template_guid, chest_uid, remaining in summary["opened"]:
            if remaining:
                # An inventory chest can be a stack; keep the entry
                # with its authoritative remaining count.
                handler.push_inventory_to_client(
                    qty=remaining, template_guid=template_guid,
                    item_id=chest_uid)
            else:
                handler._send_inventory_updated(
                    template_guid, chest_uid, quantity=0)


CHEST_REQUEST_HANDLERS = {
    2049: handle_chest_spinning_2049,
    2127: handle_opencardpack_2127,
    2129: handle_reserved_for_chests_with_a_2129,
}


def handle_chest_request(handler, target, instance, reqid, comp, session_id, conh,
                         *, inner_obj, inner_bytes, data_type, **_kw):
    """Route one chest request to its handler by ``data_type``."""
    return CHEST_REQUEST_HANDLERS[data_type](
        handler, target, instance, data_type, reqid, comp, session_id, conh,
        inner_obj, inner_bytes, b"")
