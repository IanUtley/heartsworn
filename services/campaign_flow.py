from domain.enums import EGemTypesNew
import json
import time
import campaign_rules
import pve_db
from profile_db import (db_add_stardust,
                        db_inventory_item, db_next_inventory_client_uid,
                        db_upsert_inventory_item, db_reward_card_template,
                        db_adjust_user_currency, db_champion_reward_profile,
                        db_update_champion_xp, db_create_treasure_chest)
import campaign as _campaign


def _materialize_quest_objectives(db, champ_id, quest_script, objectives):
    """Add faction-correct, runtime-resolvable links to quest objectives."""
    try:
        materialized = json.loads(json.dumps(objectives or []))
    except (TypeError, ValueError, json.JSONDecodeError):
        materialized = []
    for objective in materialized:
        if not isinstance(objective, dict):
            continue
        resolved_scene = _campaign._quest_objective_scene_guid(
            db, quest_script, objective)
        if resolved_scene:
            objective["encounter"] = resolved_scene
        if str(objective.get("type") or "").lower() in {
                "conversation", "convo"}:
            conversations = _campaign._quest_objective_conversation_guids(
                db, champ_id, quest_script, objective)
            if conversations:
                objective["conversation_ids"] = conversations
                objective["conversation"] = conversations[0]
    return materialized


def _ensure_quest_campaign(db, champ_id, campaign_group, quest_script=None):
    """Create the QUEST campaign row that drives the zone's quest journal.

    The client's QuestMgr caches the quest by the getactive TemplateName
    (QuestScriptName), resolves the QuestTemplate by that name, then renders
    the objectives from the QUEST campaign's GameplayState VisLocs (matched by
    objective m_QuestLocationId). Idempotent.
    """
    candidate = _campaign._quest_template(db, quest_script, campaign_group)
    quest = ((candidate["script_name"], candidate)
             if candidate else None)
    if not quest:
        return None
    script_name, q = quest
    # QuestTemplate is shared by the two AZ1 factions. Materialize the
    # faction-correct completion conversation and resolve legacy empty
    # encounter links before copying objectives into persistent quest state.
    q["objectives"] = _materialize_quest_objectives(
        db, champ_id, script_name, q.get("objectives"))
    existing_id = pve_db.db_quest_campaign_id(champ_id, script_name, db)
    if existing_id:
        return existing_id
    cid = _campaign._new_camp_id(db)
    inst_id = _campaign._generate_inst_id()
    champ = _campaign._get_champion(db, champ_id)
    champ_name = champ[3] if champ else ""
    # Reveal objectives progressively: only the FIRST objective is visible
    # initially. The client's journal iterates all quest-state VisLocs, so a
    # not-yet-reached objective (e.g. "Report your success") must NOT be in the
    # VisLocs yet — it is appended by _advance_quest_campaign when the previous
    # objective completes. The full ordered list is kept in Flags.
    vislocs = []
    locnodes = []
    for i, obj in enumerate(q["objectives"]):
        if i > 0:
            break  # only the active (first) objective for now
        # Preserve the authored objective type.  In particular, AZ1 taming
        # objectives are Encounter locations; collapsing every non-Dungeon
        # objective to Convo leaves the quest journal pointing at a synthetic
        # conversation instead of the battle.
        otype = obj.get("type") or "Convo"
        vislocs.append({
            "Data": {
                "name": obj["id"], "node": obj["id"], "type": otype,
                "autostart": False, "autopan": False, "autotrigger": False,
                "battle": None, "completed": False, "enabled": True, "visible": True,
                "repeatable": False, "givequest": False, "turninquest": False,
                "impassable": False, "unknown": False,
                "encounter": obj.get("encounter"), "encounter_desc": None, "allow_cancel": False,
                "conversationId": obj.get("conversation"),
            }
        })
        locnodes.append({"Name": obj["id"], "Data": {"id": obj["id"], "type": "DEFAULT"}})
    state = {
        "CampID": cid,
        "ChampID": champ_id,
        "TempType": "QUEST",
        "PayGroups": [],
        "CSlide": None,
        "ALoc": q["objectives"][0]["id"] if q["objectives"] else "",
        "VisLocs": vislocs,
        "LocNodes": locnodes,
        # The client resolves Location.Encounter through this catalog before
        # it can open a battle panel. Build it from the encounter_scenes rows
        # referenced by the authored objectives.
        "Encounters": [
            {"Name": guid, "Data": {"encscene": guid}}
            for guid in dict.fromkeys(
                obj.get("encounter") for obj in q["objectives"]
                if obj.get("encounter") and pve_db.db_encounter_scene_exists(
                    obj["encounter"], db)
            )
        ],
        "Champions": [],
        "CurState": "EXPLORE",
        "LastNode": q["objectives"][0]["id"] if q["objectives"] else "",
        # The client's quest tracker draws only quests whose group matches the
        # zone, so a start from the engine (no group given) takes the template's.
        "PublicState": {"Data": _campaign._quest_initial_public_data(db, champ_id, campaign_group, q)},
        "Started": None, "Finished": None, "FinishReason": None,
        "Wins": 0, "Losses": 0, "Score": 0, "HealthAdj": 0, "DungeonLifeAdj": 0,
        "Flags": {"_quest_objective_idx": 0, "_quest_objectives": q["objectives"]},
    }
    pve_db.db_create_campaign(
        cid, inst_id, champ_id, pve_db.db_campaign_user_id(champ_id, db),
        champ_name, script_name, "QUEST", json.dumps(state), db, is_started=True)
    db.commit()
    return cid


def _advance_quest_campaign(db, champ_id, quest_script=None, scene_guid=None):
    """Complete the current quest objective and reveal the next one.

    The client's journal iterates all quest-state VisLocs, so objectives that
    are not yet reachable must not be present. This marks the active objective
    completed and appends the next objective's VisLoc when one exists. Returns
    the updated GameplayState or None.
    """
    if not quest_script:
        template = _campaign._quest_template(db, campaign_group="DUNGEON")
        quest_script = template["script_name"] if template else None
    if not quest_script:
        return None
    rows = pve_db.db_campaign_state_rows(
        champ_id, "QUEST", db, template_name=quest_script)
    if not rows:
        return None
    qid, state_json = rows[0]
    state = json.loads(state_json) if state_json else None
    if not state:
        return None
    flags = state.setdefault("Flags", {})
    objectives = flags.get("_quest_objectives") or []
    materialized = _materialize_quest_objectives(
        db, champ_id, quest_script, objectives)
    if materialized != objectives:
        objectives = materialized
        flags["_quest_objectives"] = objectives
    idx = int(state.get("Flags", {}).get("_quest_objective_idx", 0))
    if scene_guid and idx < len(objectives):
        expected = objectives[idx].get("encounter")
        if expected and str(expected) != str(scene_guid):
            return None
    # Mark the current objective completed.
    cur_name = objectives[idx]["id"] if idx < len(objectives) else None
    for loc in state.get("VisLocs", []):
        d = loc.get("Data", {})
        if cur_name and (d.get("node") == cur_name or d.get("name") == cur_name):
            d["completed"] = True
    # Reveal the next objective if any.
    nxt = idx + 1
    if nxt < len(objectives):
        obj = objectives[nxt]
        otype = obj.get("type") or "Convo"
        # The post-dungeon journal objective is a real conversation.  Resolve
        # its GUID from the race-specific Crayburn quest-end conversation
        # instead of emitting a null conversationId that makes the client
        # hang in campaign loading. The final encounter's victory scene is
        # shown at the castle; the quest objective is the later report to the
        # faction NPC in the AZ1 panorama.
        conversation_id = None
        quest_template = _campaign._quest_template(db, quest_script)
        if (otype.lower() in {"convo", "conversation"} and
                quest_template and
                quest_template.get("campaign_group") == "DUNGEON"):
            conversation_id = _campaign._crayburn_quest_end_conv(
                db, _campaign._race_name_for_campaign(db, champ_id))
        elif otype.lower() in {"convo", "conversation"}:
            conversation_id = obj.get("conversation")
        state.setdefault("VisLocs", []).append({
            "Data": {
                "name": obj["id"], "node": obj["id"], "type": otype,
                "autostart": False, "autopan": False, "autotrigger": False,
                "battle": None, "completed": False, "enabled": True, "visible": True,
                "repeatable": False, "givequest": False, "turninquest": False,
                "impassable": False, "unknown": False,
                # Encounter objectives keep their scene link (Tamed's next
                # capture); the retry/complete bookkeeping matches on it.
                "encounter": (obj.get("encounter")
                              if otype.lower() == "encounter" else None),
                "encounter_desc": None, "allow_cancel": False,
                "conversationId": conversation_id,
            }
        })
        state.setdefault("LocNodes", []).append(
            {"Name": obj["id"], "Data": {"id": obj["id"], "type": "DEFAULT"}})
        state["Flags"]["_quest_objective_idx"] = nxt
        state["ALoc"] = obj["id"]
        state["LastNode"] = obj["id"]
    else:
        # The conversation/encounter just completed the final objective. Keep
        # the completed location in the journal, but close the QUEST campaign
        # so the client no longer advertises it as active.
        state["Flags"]["_quest_objective_idx"] = nxt
        state["Finished"] = _campaign._now_utc()
        state["FinishReason"] = "Complete"
        state["ALoc"] = None
        state["CurState"] = "EXPLORE"
    _campaign._save_campaign_state(db, qid, state)
    db.commit()
    return state


def handle_campaign_request(handler, _db, inner_obj, comp, session_id, reqid,
                             target, instance, conh, service_mail_uid):
    """Entry point for ServiceCampaign (dt=110000) requests.

    inner_obj: parsed dict from the outer ObjFmt message.
               Has an "Envelope" key with the JSON request bytes.
    """
    uid = service_mail_uid  # pass to all handlers
    envelope = inner_obj.get("Envelope", b"{}")
    if isinstance(envelope, bytes):
        try:
            env_json = json.loads(envelope.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            env_json = {}
    elif isinstance(envelope, str):
        try:
            env_json = json.loads(envelope)
        except json.JSONDecodeError:
            env_json = {}
    else:
        env_json = envelope if isinstance(envelope, dict) else {}

    req_type = env_json.get("RequestType", "")
    log = getattr(handler, "_log_req", print)
    log(f"    Campaign dt=110000 req={req_type}")

    if req_type == "qcur4champ":
        return _campaign._handle_qcur4champ(handler, _db, env_json, comp, session_id,
                                   reqid, target, instance, conh, uid)
    elif req_type == "getactive":
        return _campaign._handle_getactive(handler, _db, env_json, comp, session_id,
                                  reqid, target, instance, conh, uid)
    elif req_type == "createcamp":
        return _handle_createcamp(handler, _db, env_json, comp, session_id,
                                   reqid, target, instance, conh, uid)
    elif req_type == "getcampstate":
        return _campaign._handle_getcampstate(handler, _db, env_json, comp, session_id,
                                     reqid, target, instance, conh, uid)
    elif req_type == "startcamp":
        return _handle_startcamp(handler, _db, env_json, comp, session_id,
                                  reqid, target, instance, conh, uid)
    elif req_type == "getcampsum":
        return _campaign._handle_getcampsum(handler, _db, env_json, comp, session_id,
                                   reqid, target, instance, conh, uid)
    elif req_type == "sendevent":
        return _campaign._handle_sendevent(handler, _db, env_json, comp, session_id,
                                  reqid, target, instance, conh, uid)
    elif req_type == "gameend":
        return _campaign._handle_gameend(handler, _db, env_json, comp, session_id,
                                reqid, target, instance, conh, uid)
    elif req_type == "locaction":
        return _campaign._handle_locaction(handler, _db, env_json, comp, session_id,
                                  reqid, target, instance, conh, uid)
    elif req_type == "forfeit":
        return _campaign._handle_forfeit(handler, _db, env_json, comp, session_id,
                                reqid, target, instance, conh, uid)
    elif req_type == "cheat":
        return _campaign._handle_cheat(handler, _db, env_json, comp, session_id,
                              reqid, target, instance, conh, uid)
    elif req_type == "partysave":
        return _campaign._handle_partysave(handler, _db, env_json, comp, session_id,
                                 reqid, target, instance, conh, uid)
    elif req_type == "partyload":
        return _campaign._handle_partyload(handler, _db, env_json, comp, session_id,
                                 reqid, target, instance, conh, uid)
    else:
        log(f"    Unhandled campaign request: {req_type} "
            f"{json.dumps(env_json, default=str)[:4000]}")
        return


def _handle_createcamp(handler, db, env_json, comp, session_id,
                        reqid, target, instance, conh, uid):
    """CreateCamp: create a new campaign instance."""
    champ_id = env_json.get("ChampID", 0)
    requested_name = str(env_json.get("Template") or "Crayburn Castle").strip()
    template_name = requested_name
    from campaign_runtime import catalog_data, rules_loader, runtime

    # camp.Dungeon passes its name through GetActiveStatus/CreateCamp. Resolve
    # it against the authored zone catalogue so each named dungeon gets its own
    # template and rules runtime instead of silently reusing Castle Crayburn.
    dungeon_zone = catalog_data.resolve_zone_name(requested_name, kind="dungeon")
    if dungeon_zone is None and _campaign._is_known_dungeon(requested_name):
        # Keep the old forgiving spelling behaviour for Castle Crayburn.
        dungeon_zone = catalog_data.resolve_zone_name(
            "Castle Crayburn", kind="dungeon")
    if dungeon_zone:
        campaign_type = "DUNGEON"
        template_name = dungeon_zone.get("server_template") or dungeon_zone["title"]
        try:
            runtime_available = rules_loader.runtime_enabled(dungeon_zone["id"])
        except Exception as exc:
            runtime_available = False
            print(f"[campaign] dungeon runtime unavailable for "
                  f"{dungeon_zone['id']}: {exc}")
        if not runtime_available:
            resp = _campaign._build_input_response(0, None, success=False)
            resp["Errors"] = [
                f"Dungeon {dungeon_zone.get('title') or template_name!r} "
                "does not have a server campaign runtime"
            ]
            return _campaign._send_response(handler, json.dumps(resp), comp, session_id,
                                  reqid, target, instance, conh, uid)
    elif "panorama" in requested_name.lower():
        campaign_type = "PANORAMA"
    else:
        campaign_type = "PANORAMA"

    champ = _campaign._get_champion(db, champ_id)
    if not champ:
        resp = _campaign._build_input_response(0, None, success=False)
        resp["Errors"] = ["Champion not found"]
        return _campaign._send_response(handler, json.dumps(resp), comp, session_id,
                              reqid, target, instance, conh, uid)

    if dungeon_zone:
        # Dungeons are separate campaign templates. A type-only lookup here
        # returned the champion's existing Crayburn row for every debug name.
        from campaign_runtime import progress

        with progress.champion_lock(champ_id):
            row = pve_db.db_campaign_for_champion_template(
                champ_id, template_name, conn=db)
            if row:
                cid, inst_id, is_started, state_json, stored_type = row
                try:
                    state = json.loads(state_json) if state_json else {}
                except (TypeError, ValueError):
                    state = {}
                old_public = state.get("PublicState") if isinstance(state, dict) else None
                old_data = (old_public.get("Data")
                            if isinstance(old_public, dict) else None)
                old_group = (old_data.get("CampaignGroup")
                             if isinstance(old_data, dict) else None)
                if (str(stored_type or "").upper() != "DUNGEON" or not state or
                        not isinstance(state, dict) or
                        str(state.get("TempType") or "").upper() not in
                        ("", "DUNGEON") or
                        old_group not in (None, "DUNGEON") or
                        state.get("RulesZone") not in (None, dungeon_zone["id"])):
                    state = _campaign._initial_zone_state(
                        cid, champ_id, dungeon_zone["id"])
                else:
                    state["CampID"] = cid
                    state["ChampID"] = champ_id
                    state["TempType"] = "DUNGEON"
                    state["RulesZone"] = dungeon_zone["id"]
                    public = state.get("PublicState")
                    if not isinstance(public, dict):
                        public = {}
                    data = public.get("Data")
                    if not isinstance(data, dict):
                        data = {}
                    data.setdefault("CampaignGroup", "DUNGEON")
                    public["Data"] = data
                    state["PublicState"] = public
                pve_db.db_campaign_set_zone(
                    cid, "DUNGEON", template_name, json.dumps(state), conn=db,
                    is_started=is_started)
                state = runtime.refresh_projection(db, cid, dungeon_zone["id"])
            else:
                cid = _campaign._new_camp_id(db)
                inst_id = _campaign._generate_inst_id()
                state = _campaign._initial_zone_state(
                    cid, champ_id, dungeon_zone["id"])
                pve_db.db_create_campaign(
                    cid, inst_id, champ_id, champ[1], champ[3], template_name,
                    "DUNGEON", json.dumps(state), db)
            pve_db.db_campaign_set_last_campaign(champ_id, cid, db)
            if dungeon_zone["id"] == "crayburn":
                # Preserve Crayburn's existing journal setup; other named
                # dungeons are launched directly for debugging.
                _ensure_quest_campaign(db, champ_id, "DUNGEON")
            db.commit()
    else:
        cid, inst_id, is_started, state = _campaign._find_campaign_for_champion(
            db, champ_id, campaign_type)
    resp = _campaign._build_input_response(cid, state, success=True)
    return _campaign._send_response(handler, json.dumps(resp), comp, session_id,
                          reqid, target, instance, conh, uid)


def _handle_getcampstate(handler, db, env_json, comp, session_id,
                          reqid, target, instance, conh, uid):
    """QueryCampState: return full GameplayState for a campaign."""
    camp_id = env_json.get("CampID", 0)
    zone_id = _campaign._rules_zone(db, camp_id)
    if zone_id:
        from campaign_runtime import runtime
        return runtime.handle_query(handler, db, zone_id, env_json, comp,
                                    session_id, reqid, target, instance, conh, uid)
    # A quest campaign is not a zone: its journal row is its state.
    row = pve_db.db_campaign_query_row(camp_id, conn=db)
    if not row or str(row[3] or "").upper() != "QUEST":
        return _campaign._no_zone_reply(handler, camp_id, comp, session_id, reqid,
                              target, instance, conh, uid)
    state = json.loads(row[2] or "{}")
    resp = _campaign._build_input_response(camp_id, state, success=True)
    return _campaign._send_response(handler, json.dumps(resp), comp, session_id,
                          reqid, target, instance, conh, uid)


def _handle_startcamp(handler, db, env_json, comp, session_id,
                       reqid, target, instance, conh, uid):
    """StartCamp: mark a campaign as started, return updated state."""
    camp_id = env_json.get("CampID", 0)
    zone_id = _campaign._rules_zone(db, camp_id)
    if zone_id:
        from campaign_runtime import runtime
        return runtime.handle_query(handler, db, zone_id, env_json, comp,
                                    session_id, reqid, target, instance, conh,
                                    uid, start=True)
    row = pve_db.db_campaign_protocol_row(camp_id, conn=db)
    if not row or str(row[2] or "").upper() != "QUEST":
        return _campaign._no_zone_reply(handler, camp_id, comp, session_id, reqid,
                              target, instance, conh, uid)
    champ_id, state_json, ctype, template_name = row
    state = json.loads(state_json or "{}")
    state["Started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["CurState"] = "EXPLORE"
    pve_db.db_campaign_update_state(
        camp_id, json.dumps(state), conn=db, started=True)
    db.commit()
    resp = _campaign._build_input_response(camp_id, state, success=True)
    return _campaign._send_response(handler, json.dumps(resp), comp, session_id,
                          reqid, target, instance, conh, uid)


def _handle_sendevent(handler, db, env_json, comp, session_id,
                       reqid, target, instance, conh, uid):
    """SendEvent: handle events sent to the campaign logic engine."""
    camp_id = env_json.get("CampID", 0)
    if str(env_json.get("Event", "") or "") == "start":
        _campaign._remember_mercenary_choice(handler, db, camp_id, env_json.get("OParms"))
    zone_id = _campaign._rules_zone(db, camp_id)
    if zone_id:
        from campaign_runtime import runtime
        return runtime.handle_sendevent(handler, db, zone_id, env_json, comp,
                                        session_id, reqid, target, instance,
                                        conh, uid)
    return _campaign._no_zone_reply(handler, camp_id, comp, session_id, reqid,
                          target, instance, conh, uid)


def _apply_conversation_rewards(handler, db, camp_id, state,
                                conversation_guid):
    """Apply a conversation's authored reward and return AppliedUpdates.

    Conversation completion is a campaign request, so its rewards must be
    returned in the InputResponse's ``Applied`` payload.  This is the same
    payload used by the campaign loot window after a battle.  Claims live in
    campaign state so reconnects or a repeated ``conv_done`` cannot duplicate
    a one-time reward.
    """
    if not conversation_guid:
        return _campaign._empty_applied_updates()
    row = pve_db.db_conversation_reward(conversation_guid, conn=db)
    if not row or not row[2]:
        return _campaign._empty_applied_updates()
    reward_raw, one_time, _enabled = row
    try:
        reward = json.loads(reward_raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        reward = {}
    return _campaign._apply_reward_dict(
        handler, db, camp_id, state, reward if isinstance(reward, dict) else {},
        bool(one_time), str(conversation_guid),
        f"conversation={conversation_guid}")


def _apply_quest_rewards(handler, db, camp_id, state, quest_script):
    """Grant a quest's authored completion reward (``quest_templates.rewards_json``).

    Called where a quest is marked finished; one-time per champion, keyed
    ``quest:<script>`` in the same claim table conversations use.
    """
    row = pve_db.db_quest_template_reward(quest_script, conn=db)
    if not row or not row[1]:
        return _campaign._empty_applied_updates()
    try:
        reward = json.loads(row[0] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        reward = {}
    return _campaign._apply_reward_dict(
        handler, db, camp_id, state, reward if isinstance(reward, dict) else {},
        True, f"quest:{quest_script}", f"quest={quest_script}")


def _apply_encounter_end_rewards(handler, db, session, camp_id, won,
                                 in_dungeon=False):
    """Apply authored encounter rewards and return campaign AppliedUpdates.

    Campaign clients build their loot window from ``gameendnotify.Applied``;
    generic profile reward events are not sufficient here. The engine has
    already run the scene's flag actions; its flags are mirrored into the
    state the ``when`` tags read.
    """
    result = {"applied": _campaign._empty_applied_updates(), "cards": [],
              "items": [], "chests": [], "gold": 0, "xp": 0,
              "condition_met": False, "scene_guid": None}
    if not won:
        return result
    row = pve_db.db_campaign_identity_state(camp_id, db)
    if not row:
        return result
    champion_id, state_json = row
    try:
        state = json.loads(state_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        state = {}
    scene_guid = state.get("ActiveEncounterGuid") or _campaign._last_encounter_scene.get(
        session.session_name or "")
    champ = _campaign._get_champion(db, champion_id)
    player_user_id = champ[1] if champ else None
    records = list(_campaign._scene_reward_records(db, scene_guid) or ())
    talents = []
    if player_user_id is not None:
        talents = _campaign._champion_reward_talents(
            db, champion_id,
            mercenary=_campaign._battle_uses_mercenary(db, camp_id, player_user_id))
        if in_dungeon:
            talent_items = _campaign._talent_dungeon_reward_specs(talents)
            if talent_items:
                records.append({"reward": {"items": talent_items},
                                "one_time": False})
    if not records:
        return result
    result["scene_guid"] = str(scene_guid) if scene_guid else None
    if player_user_id is None:
        return result
    claims = state.setdefault("_encounter_reward_claims", {})
    if not isinstance(claims, dict):
        claims = {}
        state["_encounter_reward_claims"] = claims
    success_counts = state.setdefault("_encounter_successes", {})
    if not isinstance(success_counts, dict):
        success_counts = {}
        state["_encounter_successes"] = success_counts
    try:
        prior_successes = int(success_counts.get(str(scene_guid), 0) or 0)
    except (TypeError, ValueError):
        prior_successes = 0
    # Older campaign states did not have _encounter_successes. Existing
    # reward claims still prove that this scene has already been won once.
    if not prior_successes:
        claim_prefix = f"{scene_guid}:"
        if any(str(key).startswith(claim_prefix) for key in claims):
            prior_successes = 1
    quest_pending = _campaign._quest_encounter_is_pending(
        db, champion_id, str(scene_guid) if scene_guid else None)
    retry_without_currency = bool(quest_pending and prior_successes)
    champ_level = int(champ[4] or 0) if champ else 0
    xp_mult, gold_mult = _campaign._talent_reward_multipliers(talents, champ_level)
    granted, total_gold, total_xp = [], 0, 0
    for record_index, record in enumerate(records):
        reward_obj = record.get("reward")
        reward_obj = reward_obj if isinstance(reward_obj, dict) else record
        one_time = record.get("one_time")
        if one_time is None:
            one_time = reward_obj.get("one_time", True)
        one_time = bool(one_time)
        claim_key = (f"{scene_guid}:{record_index}" if one_time else
                     f"{scene_guid}:{record_index}:{session.session_id}")
        if claims.get(claim_key) or (one_time and _campaign._champion_claimed_elsewhere(
                db, champion_id, (claim_key,))):
            continue
        # ``when``: flag tags that must hold (set by the battle's events via
        # the rules hooks, see _record_battle_flags).
        when = record.get("when")
        if when:
            if not _campaign._conversation_tags_hold(db, champion_id, state, "", when):
                continue
            result["condition_met"] = True
        condition = record.get("end_of_game_condition")
        context = ({"owner": "player"} if not condition else
                   _campaign._evaluate_encounter_condition(
                       db, session.session_id, player_user_id, condition))
        if condition and context:
            result["condition_met"] = True
        try:
            gold = max(0, int(reward_obj.get("gold", 0) or 0))
        except (TypeError, ValueError):
            gold = 0
        try:
            xp = max(0, int(reward_obj.get("xp", 0) or 0))
        except (TypeError, ValueError):
            xp = 0
        if retry_without_currency:
            gold = 0
            xp = 0
        gold = _campaign._scale_reward(gold, gold_mult)
        xp = _campaign._scale_reward(xp, xp_mult)
        # Tamed scenes have unconditional completion currency alongside a
        # conditional capture card. Only the card template is gated by
        # end_of_game_condition, so an uncaptured win can be retried without
        # awarding the captured card prematurely.
        template = reward_obj.get("card_guid") or reward_obj.get("template")
        template_guid = (_campaign._resolve_reward_template(template, context)
                         if (not condition or context) else None)
        cards = []
        if template_guid:
            try:
                quantity = max(1, int(reward_obj.get("quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            cards = _campaign._grant_card_reward(handler, db, player_user_id,
                                       template_guid, quantity, emit=False)
        chest_guid = (reward_obj.get("chest_guid") or
                      reward_obj.get("chest_template") or
                      reward_obj.get("pack_guid"))
        chest = None
        if chest_guid and (not condition or context):
            chest_guid = str(chest_guid)
            chest_id = db_create_treasure_chest(
                player_user_id,
                "00000000-0000-0000-0000-000000000000", "Promo", db,
                template_guid=chest_guid)
            if chest_id is None:
                raise RuntimeError("Could not persist the campaign reward chest")
            chest = {"id": int(chest_id),
                     "template": chest_guid}
        items = []
        item_specs = reward_obj.get("items") or []
        champ_race = _campaign._RACE_NAMES.get(champ[2], "") if champ else ""
        race_item_guid = (reward_obj.get("item_guid_by_race") or {}).get(champ_race)
        if race_item_guid:
            item_specs = list(item_specs) + [{
                "guid": race_item_guid, "quantity": reward_obj.get("quantity", 1),
            }]
        if reward_obj.get("item_guid"):
            item_specs = list(item_specs) + [{
                "guid": reward_obj.get("item_guid"),
                "quantity": reward_obj.get("quantity", 1),
            }]
        if isinstance(item_specs, dict):
            item_specs = [item_specs]
        if not condition or context:
            item_specs = (list(item_specs) + _campaign._stardust_item_specs(reward_obj) +
                          _campaign._inventory_reward_specs(reward_obj) +
                          _campaign._random_equipment_specs(reward_obj))
            for spec in item_specs:
                if isinstance(spec, str):
                    spec = {"guid": spec}
                if not isinstance(spec, dict):
                    continue
                template_guid = (spec.get("guid") or spec.get("template") or
                                 spec.get("item_guid"))
                if not template_guid:
                    continue
                try:
                    quantity = max(1, int(spec.get("quantity", 1) or 1))
                except (TypeError, ValueError):
                    quantity = 1
                if spec.get("stardust"):
                    db_add_stardust(player_user_id, spec["stardust"], quantity,
                                    conn=db)
                existing = db_inventory_item(
                    player_user_id, str(template_guid), conn=db)
                if existing:
                    _item_row_id, old_quantity, item_uid = existing
                    new_quantity = int(old_quantity or 0) + quantity
                    if not item_uid:
                        item_uid = db_next_inventory_client_uid(
                            player_user_id, conn=db)
                    db_upsert_inventory_item(
                        player_user_id, str(template_guid), quantity,
                        client_item_uid=item_uid, conn=db)
                else:
                    item_uid = db_next_inventory_client_uid(
                        player_user_id, conn=db)
                    new_quantity = quantity
                    db_upsert_inventory_item(
                        player_user_id, str(template_guid), new_quantity,
                        client_item_uid=item_uid, conn=db)
                items.append({"id": item_uid, "template": str(template_guid),
                              "quantity": new_quantity, "granted": quantity})
                if spec.get("_campaign_mercenary"):
                    _campaign._unlock_party_slot_for_mercenary(
                        db, player_user_id, result["applied"])
        deck_granted = False
        deck_guid = reward_obj.get("deck_guid")
        if deck_guid and (not condition or context):
            deck_granted = _campaign._grant_reward_deck(
                handler, db, player_user_id, str(deck_guid).lower())
        if (not cards and not gold and not xp and not chest and not items and
                not deck_granted):
            continue
        granted.extend(cards)
        total_gold += gold
        total_xp += xp
        result["items"].extend(items)
        if chest:
            result["chests"].append(chest)
        claims[claim_key] = True

    success_counts[str(scene_guid)] = prior_successes + 1

    account = None
    if total_gold:
        new_gold, platinum = db_adjust_user_currency(
            player_user_id, gold_delta=total_gold, conn=db)
        account = {"Gold": new_gold, "Platinum": platinum}

    champion_bits = None
    if total_xp:
        crow = db_champion_reward_profile(champion_id, conn=db)
        if crow:
            new_xp = int(crow[3] or 0) + total_xp
            new_level = _campaign._champion_level_for_xp(new_xp)
            db_update_champion_xp(champion_id, new_xp, new_level, conn=db)
            champion_bits = {
                "Id": int(crow[0]), "Name": crow[1] or "",
                "Level": new_level, "CurrentXP": new_xp,
                "ChampionClass": int(crow[4] or 0), "Race": int(crow[5] or 0),
                "Gender": int(crow[6] or 0),
                "LastCampaignID": int(crow[7] or 0),
                "LastDeckID": int(crow[8] or 0), "IsDeleted": bool(crow[9]),
                "LastRespec": 0, "FreeRespec": 0,
                "PetName": crow[10] or "",
            }

    applied = result["applied"]
    if account:
        applied["Accounts"].append({"Account": account})
    if champion_bits:
        applied["Champions"].append({"Champ": champion_bits})
    if total_gold:
        applied["Completed"].append({"ItemKind": "GOLD", "ItemQuantity": total_gold,
                                      "ItemAction": "GRANT", "ItemTemplate": "",
                                      "RCode": "GOLD"})
    if total_xp:
        applied["Completed"].append({"ItemKind": "XP", "ItemQuantity": total_xp,
                                      "ItemAction": "GRANT", "ItemTemplate": "",
                                      "RCode": "XP"})
    for card in granted:
        applied["Cards"].append({"Card": {
            "Id": int(card["instance_id"]), "TemplateID": card["guid"],
            "CardStats": {}, "IsFoil": False, "IsExtended": False,
            "SocketedGems": EGemTypesNew.Unknown, "IsNotTradeable": False,
            "EscrowStatus": "Clean",
        }})
        applied["Completed"].append({
            "ItemKind": "BOACARD", "ItemQuantity": 1,
            "ItemAction": "GRANT", "ItemTemplate": card["guid"],
            "RCode": "CARD",
        })
    for chest in result["chests"]:
        applied["Items"].append({"Item": {
            "Id": 9000 + int(chest["id"]),
            "TemplateID": chest["template"],
            "BoundToProfile": True,
            "ItemQuantity": 1,
            "ClaimDate": "0001-01-01T00:00:00",
            "EscrowStatus": "Clean",
        }})
        applied["Completed"].append({
            "ItemKind": "BOAITEM", "ItemQuantity": 1,
            "ItemAction": "GRANT", "ItemTemplate": chest["template"],
            "RCode": "CHEST",
        })
    for item in result["items"]:
        applied["Items"].append({"Item": {
            "Id": int(item["id"]),
            "TemplateID": item["template"],
            "BoundToProfile": True,
            "ItemQuantity": int(item["quantity"]),
            "ClaimDate": "0001-01-01T00:00:00",
            "EscrowStatus": "Clean",
        }})
        applied["Completed"].append({
            "ItemKind": "BOAITEM", "ItemQuantity": int(item["granted"]),
            "ItemAction": "GRANT", "ItemTemplate": item["template"],
            "RCode": "ITEM",
        })
    if (not granted and not total_gold and not total_xp and
            not result["chests"] and not result["items"]):
        state["_last_encounter_condition_met"] = bool(result["condition_met"])
        _campaign._save_campaign_state(db, camp_id, state)
        db.commit()
        return result
    state["_last_encounter_condition_met"] = bool(result["condition_met"])
    _campaign._save_campaign_state(db, camp_id, state)
    db.commit()
    result.update({"cards": granted, "gold": total_gold, "xp": total_xp})
    getattr(handler, "_log_req", print)(
        f"    Encounter reward: scene={scene_guid} cards={len(granted)} "
        f"chests={len(result['chests'])} gold={total_gold} xp={total_xp}")
    return result


def preview_encounter_rewards(db, session, scene_guid, user_id, request):
    """What ``_apply_encounter_end_rewards`` pays for a first win of
    ``scene_guid``, without granting anything (an editor Play battle).

    Returns ``{"display": [...], "lines": [...]}``: ``display`` entries feed
    the display-only ``push_display_rewards`` popup (gold, cards, items,
    chests; the popup has no XP entry), ``lines`` describe every reward for
    the editor. ``when`` tags read the editor's simulated state
    (``_playtest_tag_view``); one-time claims and the quest-retry currency
    rule are not applied because the playtest has no campaign state."""
    out = {"display": [], "lines": []}
    records = _campaign._scene_reward_records(db, scene_guid)
    if not records:
        return out
    battle = request.get("battle") or {}
    champ = (_campaign._get_champion(db, battle["champion_id"])
             if battle.get("champion_id") else None)
    race_name = _campaign._RACE_NAMES.get(champ[2], "") if champ else ""
    view = _campaign._playtest_tag_view(db, session, scene_guid, user_id, request, race_name)

    def show(kind, template, quantity, label):
        out["display"].append({"id": "playtest", "template": str(template or ""),
                               "quantity": int(quantity), "type": kind,
                               "ledger_id": 0, "boa": True})
        out["lines"].append(f"{label} x{int(quantity)}")

    for record in records:
        reward_obj = record.get("reward")
        reward_obj = reward_obj if isinstance(reward_obj, dict) else record
        when = record.get("when")
        if when:
            try:
                if not campaign_rules.evaluate(when, view, ""):
                    continue
            except campaign_rules.RulesError:
                continue
        condition = record.get("end_of_game_condition")
        context = ({"owner": "player"} if not condition else
                   _campaign._evaluate_encounter_condition(
                       db, session.session_id, user_id, condition))
        met = not condition or bool(context)
        try:
            gold = max(0, int(reward_obj.get("gold", 0) or 0))
            xp = max(0, int(reward_obj.get("xp", 0) or 0))
            quantity = max(1, int(reward_obj.get("quantity", 1) or 1))
        except (TypeError, ValueError):
            gold, xp, quantity = 0, 0, 1
        if gold:
            show("GOLD", "", gold, "gold")
        if xp:
            out["lines"].append(f"xp x{xp}")
        template = reward_obj.get("card_guid") or reward_obj.get("template")
        card_guid = _campaign._resolve_reward_template(template, context) if met else None
        if card_guid:
            card = db_reward_card_template(str(card_guid), conn=db)
            show("CARD", card_guid, quantity, f"card {card[0] if card else card_guid}")
        chest_guid = (reward_obj.get("chest_guid") or
                      reward_obj.get("chest_template") or reward_obj.get("pack_guid"))
        if chest_guid and met:
            show("CHEST", chest_guid, 1, f"chest {chest_guid}")
        if not met:
            continue
        item_specs = reward_obj.get("items") or []
        if isinstance(item_specs, dict):
            item_specs = [item_specs]
        item_specs = list(item_specs)
        race_item = (reward_obj.get("item_guid_by_race") or {}).get(race_name)
        if race_item:
            item_specs.append({"guid": race_item, "quantity": quantity})
        if reward_obj.get("item_guid"):
            item_specs.append({"guid": reward_obj["item_guid"], "quantity": quantity})
        for spec in (item_specs + _campaign._stardust_item_specs(reward_obj) +
                     _campaign._inventory_reward_specs(reward_obj)):
            spec = {"guid": spec} if isinstance(spec, str) else spec
            if not isinstance(spec, dict):
                continue
            guid = spec.get("guid") or spec.get("template") or spec.get("item_guid")
            if not guid:
                continue
            try:
                count = max(1, int(spec.get("quantity", 1) or 1))
            except (TypeError, ValueError):
                count = 1
            label = (f"stardust {spec['stardust']}" if spec.get("stardust")
                     else f"item {guid}")
            show("INVENTORY", guid, count, label)
        if reward_obj.get("deck_guid"):
            from services.store import deck_template_definition
            deck = deck_template_definition(str(reward_obj["deck_guid"]).lower())
            out["lines"].append(
                f"deck {deck['name'] if deck else reward_obj['deck_guid']}")
    return out


def _rules_battle_gameend(handler, db, session, camp_id, won, zone_id,
                          service_mail_uid, service_campaign_uid_type):
    """``handle_battle_gameend`` for a rules-driven zone: the engine applies
    the result (scene / card-choice flag actions, quest steps, result
    conversation), then the scene's rewards are paid against its flags."""
    from campaign_runtime import runtime
    from pvp_db import db_delete_game_session
    zone = runtime.handle_battle_end(handler, db, zone_id, session, camp_id, won)
    reward_result = _campaign._apply_encounter_end_rewards(
        handler, db, session, camp_id, won,
        in_dungeon=_campaign._zone_is_dungeon(zone_id))
    applied = _campaign._merge_applied(reward_result.get("applied") or
                             _campaign._empty_applied_updates(), zone.applied)
    service = ("00000000-0000-0000-0000-000000000000", "ServiceCampaign",
               str(service_campaign_uid_type), 0, service_mail_uid)
    _campaign.push_gameendnotify(handler, db, camp_id, won, 0, *service, applied=applied)
    net = {"handler": handler, "args": (0,) + service}
    for push in zone.after:
        push(net)
    db_delete_game_session(session.session_id)
    getattr(handler, "_log_req", print)(
        f"    Campaign {camp_id} [{zone_id}]: rules result applied (won={won})")
    return True


def handle_battle_gameend(handler, db, session, won, service_mail_uid,
                          service_campaign_uid_type=253):
    """Apply a finished PvE/FRA battle and clean up its game session.

    This is intentionally kept in the campaign module because the battle
    protocol only reports the result; campaign progression, notifications, and
    session lifecycle are service concerns.  FRA persistence remains here as
    the same result path is shared by practice and campaign battles.
    """
    from pvp_db import db_delete_game_session
    try:
        session_name = session.session_name or ""
        if not session_name.startswith("camp_"):
            prepared = _campaign.prepare_fra_battle_gameend(handler, db, session, won)
            if prepared is not None:
                _campaign.publish_fra_battle_gameend(
                    handler, prepared, service_mail_uid)
                getattr(handler, "_log_req", print)(
                    f"    FRA result recorded (won={won}); session cleaned")
                return bool(prepared.get("handled"))
            return False
        camp_id = int(session_name[5:])
        import campaign_playtest
        if campaign_playtest.is_playtest_campaign(camp_id):
            # An editor Play battle: show its rewards, change no campaign.
            return campaign_playtest.finish_battle(
                handler, db, session, camp_id, won)
        zone_id = _campaign._rules_zone(db, camp_id)
        if zone_id:
            return _rules_battle_gameend(handler, db, session, camp_id, won,
                                         zone_id, service_mail_uid,
                                         service_campaign_uid_type)
        db_delete_game_session(session.session_id)
        getattr(handler, "_log_req", print)(
            f"    Campaign {camp_id}: no rules zone; session cleaned")
        return False
    except Exception as exc:
        getattr(handler, "_log_req", print)(
            f"    gameendnotify failed: {exc}")
        return False


def _handle_gameend(handler, db, env_json, comp, session_id,
                     reqid, target, instance, conh, uid):
    """Handle a campaign game-end notification (RequestType "gameend").

    The client sends this after a campaign battle finishes, with the session
    name and winner/loser UID lists. After the training battle is won, reveal
    the quest-giver NPC so the player can take the Crayburn Castle quest.
    """
    camp_id = env_json.get("CampID", 0)
    zone_id = _campaign._rules_zone(db, camp_id)
    if zone_id:
        from campaign_runtime import runtime
        return runtime.handle_gameend(handler, db, zone_id, env_json, comp,
                                      session_id, reqid, target, instance,
                                      conh, uid)
    return _campaign._no_zone_reply(handler, camp_id, comp, session_id, reqid,
                          target, instance, conh, uid)


def _handle_locaction(handler, db, env_json, comp, session_id,
                       reqid, target, instance, conh, uid):
    """StartLoc / FinishLoc: start or finish a location."""
    camp_id = env_json.get("CampID", 0)
    zone_id = _campaign._rules_zone(db, camp_id)
    if zone_id:
        from campaign_runtime import runtime
        return runtime.handle_locaction(handler, db, zone_id, env_json, comp,
                                        session_id, reqid, target, instance,
                                        conh, uid)
    return _campaign._no_zone_reply(handler, camp_id, comp, session_id, reqid,
                          target, instance, conh, uid)
