"""Store requests (item listing, redeem codes) still served by the legacy fallback.

Moved out of hconnect_server.HCPHandler._handle_service_request_legacy.
"""



# ServiceEscrow — GetStoreItems (6009)
def handle_getstoreitems_6009(handler, target, instance, data_type, reqid, comp, session_id,
                              conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    _hc.log_req(">>> Respond GetStoreItems -> 1 booster pack")
    resp_inner = _hc.encode_get_store_items_response()
    resp_body = _hc.compress_gzip(resp_inner) if comp else resp_inner
    resp_reqid = reqid | 1
    dw_bytes = _hc.encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
    issuer_str = f"0.0.0.0.ServiceEscrow.{_hc.SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
    handler.scnt += 1
    handler.send({
        "issuer": issuer_str,
        "target": target,
        "instance": instance,
        "reqid": resp_reqid,
        "c": comp,
        "conh": conh,
        "sid": handler.sid,
    }, dw_bytes)
    _hc.log_req(f"    Sent GetStoreItems response ({len(dw_bytes)}b)")


# ServiceEscrow — RedeemCode (6013)
def handle_redeemcode_6013(handler, target, instance, data_type, reqid, comp, session_id,
                           conh, inner_obj, inner_bytes, raw_data_wrapper_bytes):
    import hconnect_server as _hc
    redeem_code = inner_obj.get("RedeemCode", "")
    _hc.log_req(f">>> RedeemCode: code='{redeem_code}'")
    
    p = handler.user_profile
    result = _hc.db_redeem_code(redeem_code, p["id"])
    if not result["redeemed"]:
        _hc.log_req(f"    RedeemCode invalid: {redeem_code}")
        resp_inner = _hc.encode_objfmt_response(
            ["Game.Client.Network.Escrow.RedeemCodeResponse",
             "System.Collections.Generic.List`1#Game.Shared.ResourceId",
             "Game.Shared.ResourceId",
             "System.Guid",
             "System.Int32",
             "System.Collections.Generic.List`1#Game.Shared.Domain.card_instance_bits",
             "System.Collections.Generic.List`1#Game.Shared.Domain.deck_bits",
             "Game.Shared.Network.Escrow.ERedeemCodeError", "System.String"],
            [("ItemTemplateIds", "coll", ("System.Collections.Generic.List`1#Game.Shared.ResourceId", 0)),
             ("GoldDelta", "int", 0),
             ("PlatinumDelta", "int", 0),
             ("CardBits", "coll", ("System.Collections.Generic.List`1#Game.Shared.Domain.card_instance_bits", 0)),
             ("StarterDecksBits", "coll", ("System.Collections.Generic.List`1#Game.Shared.Domain.deck_bits", 0)),
             ("Error", "enum1", ("Game.Shared.Network.Escrow.ERedeemCodeError", 1)),
             ("ErrorMessage", "string", result["error_message"])]
        )
        resp_body = _hc.compress_gzip(resp_inner) if comp else resp_inner
        resp_reqid = reqid | 1
        dw_bytes = _hc.encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
        issuer_str = f"0.0.0.0.ServiceEscrow.{_hc.SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
        handler.scnt += 1
        handler.send({
            "issuer": issuer_str, "target": target, "instance": instance,
            "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
        }, dw_bytes)
        _hc.log_req(f"    Sent RedeemCode (invalid) response ({len(dw_bytes)}b)")
    else:
        gold_delta = result["gold"]
        plat_delta = result["platinum"]
        from services.store import grant_vendor_code_cards
        card_grants = grant_vendor_code_cards(p["id"], redeem_code)
        new_gold = p["gold"] + gold_delta
        new_plat = p["platinum"] + plat_delta
        _hc.db_update_resources(p["id"], gold=new_gold, platinum=new_plat)
        p["gold"] = new_gold
        p["platinum"] = new_plat
    
        parts = []
        if gold_delta > 0:
            parts.append(f"{gold_delta:,} Gold")
        if plat_delta > 0:
            parts.append(f"{plat_delta:,} Platinum")
        if card_grants:
            parts.append(f"{len(card_grants)} PvP card templates x4")
        reward_desc = ", ".join(parts)
    
        _hc.db_send_email(p["id"],
            f"Code Redeemed: {redeem_code}",
            f"You have successfully redeemed the code '{redeem_code}' and received {reward_desc}.\n\nThank you for playing Hex: Shards of Fate!",
            "SYSTEM")
    
        _hc.log_req(f"    RedeemCode success: gold+{gold_delta} plat+{plat_delta}")
        if card_grants:
            handler.push_cards_to_client()
    
        resp_inner = _hc.encode_objfmt_response(
            ["Game.Client.Network.Escrow.RedeemCodeResponse",
             "System.Collections.Generic.List`1#Game.Shared.ResourceId",
             "Game.Shared.ResourceId",
             "System.Guid",
             "System.Int32",
             "System.Collections.Generic.List`1#Game.Shared.Domain.card_instance_bits",
             "System.Collections.Generic.List`1#Game.Shared.Domain.deck_bits",
             "Game.Shared.Network.Escrow.ERedeemCodeError", "System.String"],
            [("ItemTemplateIds", "coll", ("System.Collections.Generic.List`1#Game.Shared.ResourceId", 0)),
             ("GoldDelta", "int", gold_delta),
             ("PlatinumDelta", "int", plat_delta),
             ("CardBits", "coll", ("System.Collections.Generic.List`1#Game.Shared.Domain.card_instance_bits", 0)),
             ("StarterDecksBits", "coll", ("System.Collections.Generic.List`1#Game.Shared.Domain.deck_bits", 0)),
             ("Error", "enum1", ("Game.Shared.Network.Escrow.ERedeemCodeError", 0)),
             ("ErrorMessage", "string", "")]
        )
        resp_body = _hc.compress_gzip(resp_inner) if comp else resp_inner
        resp_reqid = reqid | 1
        dw_bytes = _hc.encode_datawrapper(resp_reqid, data_type, resp_body, comp, session_id)
        issuer_str = f"0.0.0.0.ServiceEscrow.{_hc.SERVICE_MAIL_UID}.ServicePlayer.{handler.client_uid}.{resp_reqid}"
        handler.scnt += 1
        handler.send_and_cache({
            "issuer": issuer_str, "target": target, "instance": instance,
            "reqid": resp_reqid, "c": comp, "conh": conh, "sid": handler.sid,
        }, dw_bytes, data_type, reqid, target, instance)
        _hc.log_req(f"    Sent RedeemCode (ok) response ({len(dw_bytes)}b)")


# OpenCardPack (2127)


# SpinWheelOfFate (2049) — chest spinning


# OpenChest (2129) — direct opening for NoSpin promo chests and for
# InventoryTreasureChest items granted as inventory (Crayburn Castle
# packs, the AZ1/AZ2 campaign boosters).  SpinWheelOfFate (2049) is
# reserved for chests with a spin sequence.



LEGACY_HANDLERS = {
    6009: handle_getstoreitems_6009,
    6013: handle_redeemcode_6013,
}
