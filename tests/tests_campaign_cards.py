"""The card editor's overlay: a shadow replaces a Records card, a set assignment
and a revert restore it, and a new card has no Records version. A card's keywords
and integer attributes save with it. Nothing is written to Records or hconnect.db."""

import copy
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CAMPAIGN_ROOT = os.path.join(ROOT, "campaign")
EDITOR = os.path.join(ROOT, "campaign_editor")
sys.path[:0] = [ROOT, CAMPAIGN_ROOT, EDITOR]

import campaign_data  # noqa: E402
import cards  # noqa: E402

BASE_GUID = "00000000-0000-4000-8000-000000000001"


def test_card_overlay_shadows_assigns_and_reverts():
    work = Path(tempfile.mkdtemp())
    campaign_data.CARD_TEMPLATES_PATH = work / "card_templates.json"
    # Work on copies of the custom sets, so the test never writes the real files.
    for name in ("CardTemplates.json", "CardSetTemplate.json"):
        (work / name).write_text((cards.CUSTOM_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    cards.CUSTOM_DIR = work
    cards.CUSTOM_CARDS_PATH = work / "CardTemplates.json"
    cards.CUSTOM_SETS_PATH = work / "CardSetTemplate.json"
    cards._CACHE.clear()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE card_templates (guid TEXT, set_guid TEXT, name TEXT, rarity TEXT, "
               "cost INTEGER, attack INTEGER, defense INTEGER, card_type TEXT, subtype TEXT, "
               "threshold_json TEXT, abilities_json TEXT, variable_cost INTEGER, "
               "socket_count INTEGER, is_pve INTEGER)")
    db.execute("INSERT INTO card_templates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (BASE_GUID, "", "Base Card", "Common", 2, 1, 1, "Troop", "",
                '{"values": [0, 2], "list": [1]}', '["abc"]', 0, 0, 0))

    shadow = cards.save_card(db, BASE_GUID, {
        "name": "Edited", "rarity": "Rare", "card_type": "Troop", "cost": 3,
        "attack": 2, "defense": 2, "threshold": [{"shard": "Blood", "count": 2}]})
    assert shadow["source"] == "edited" and shadow["card"]["name"] == "Edited"
    assert shadow["card"]["abilities"] == ["abc"]
    assert [c["source"] for c in cards.list_cards(db, "edited")["cards"]] == ["edited"]

    set_guid = cards.card_sets()[0]["guid"]
    cards.assign_to_set(db, [BASE_GUID], set_guid)
    assert cards.get_card(db, BASE_GUID)["card"]["set_guid"] == set_guid

    reverted = cards.revert_card(db, BASE_GUID)
    assert reverted["source"] == "records" and reverted["card"]["name"] == "Base Card"

    created = cards.save_card(db, "", {"name": "New Card", "rarity": "Common",
                                       "card_type": "Troop", "cost": 1})
    assert created["source"] == "new" and created["card"]["guid"] != BASE_GUID

    # Frames follow the client's BuildFrame: several shards are Multi, an artifact is
    # Artifact, one shard is that shard, colourless is Shardless, a resource is frameless.
    blood_wild = {"card_type": "Troop", "threshold": [{"shard": "Blood", "count": 2},
                                                      {"shard": "Wild", "count": 1}]}
    assert cards.frame_for(blood_wild)["frame"] == "Multi"
    assert cards.frame_for({"card_type": "Troop|Artifact", "threshold": [{"shard": "Blood", "count": 1}]})["frame"] == "Artifact"
    assert cards.frame_for({"card_type": "Troop", "threshold": [{"shard": "Ruby", "count": 1}]})["frame"] == "Ruby"
    assert cards.frame_for({"card_type": "Constant", "threshold": []})["frame"] == "Shardless"
    assert cards.frame_for({"card_type": "Resource", "threshold": []})["frame"] is None

    # Custom_sets cards are listed as custom and keep their Records-style fields.
    custom = cards.list_cards(db, "", 1000)["cards"]
    assert any(c["source"] == "custom" for c in custom)

    # Browsing one set lists only its cards; a new card made in a custom set is written
    # to that set's file, not the overlay.
    dmu = next(entry["guid"] for entry in cards.card_sets() if entry["source"] == "custom")
    in_set = cards.list_cards(db, "", 1000, set_guid=dmu)["cards"]
    assert in_set and all(c["set_guid"] == dmu for c in in_set)
    rows_before = len(campaign_data.load_card_overlay())
    added = cards.save_card(db, "", {"name": "Set Card", "rarity": "Common", "card_type": "Troop",
                                     "cost": 2, "set_guid": dmu})
    assert added["source"] == "custom" and added["card"]["set_guid"] == dmu
    assert len(campaign_data.load_card_overlay()) == rows_before
    assert "Set Card" in (work / "CardTemplates.json").read_text(encoding="utf-8")

    # Flavour: a non-empty value overrides the card's own (m_FlavorText on a custom card,
    # the overlay row on a Records card); an empty value writes nothing.
    fields = {"name": "Set Card", "rarity": "Common", "card_type": "Troop", "cost": 2, "set_guid": dmu}
    cards.save_card(db, added["card"]["guid"], dict(fields, flavor="Long ago, in Tolaria."))
    cards.save_card(db, added["card"]["guid"], dict(fields, flavor=""))
    assert cards.get_card(db, added["card"]["guid"])["card"]["flavor"] == "Long ago, in Tolaria."
    shadowed = cards.save_card(db, BASE_GUID, {"name": "Base Card", "rarity": "Common",
                                               "card_type": "Troop", "flavor": "Overlay flavour"})
    assert shadowed["card"]["flavor"] == "Overlay flavour"
    assert campaign_data.load_card_overlay()[BASE_GUID]["flavor"] == "Overlay flavour"


DIRE_TOAD = "9ef9c9d9-f2b1-b66c-a9bf-c4e013b17fb1"   # Records: Sky Guard, Crush; Deploy 1, Lethal 1


def test_card_keywords_and_int_attributes():
    import card_attributes

    work = Path(tempfile.mkdtemp())
    campaign_data.CARD_TEMPLATES_PATH = work / "card_templates.json"
    for name in ("CardTemplates.json", "CardSetTemplate.json"):
        (work / name).write_text((cards.CUSTOM_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    cards.CUSTOM_DIR = work
    cards.CUSTOM_CARDS_PATH = work / "CardTemplates.json"
    cards.CUSTOM_SETS_PATH = work / "CardSetTemplate.json"
    cards._CACHE.clear()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE card_templates (guid TEXT, set_guid TEXT, name TEXT, rarity TEXT, "
               "cost INTEGER, attack INTEGER, defense INTEGER, card_type TEXT, subtype TEXT, "
               "threshold_json TEXT, abilities_json TEXT, variable_cost INTEGER, "
               "socket_count INTEGER, is_pve INTEGER)")
    db.execute("INSERT INTO card_templates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (DIRE_TOAD, "", "Dire Toad", "Uncommon", 4, 3, 4, "Troop", "Beast",
                '{"values": [0, 0, 0, 0, 1, 0], "list": [4]}', "[]", 0, 0, 0))

    # The lists come from the client: ECardAttributes with its ToolName names, IntAttrs.
    options = cards.options()
    labels = {k["name"]: k["label"] for k in options["keywords"]}
    assert labels["Juggernaught"] == "Crush" and labels["SpiritDrain"] == "Life Drain"
    assert "Unknown" not in labels and "PreventAllDamage" not in labels   # none, and a union of two
    assert "Lethal" in {a["name"] for a in options["int_attributes"]}

    # A Records card shows its own keywords and integers (m_AttributeFlags, its TAC).
    card = cards.get_card(db, DIRE_TOAD)["card"]
    assert card["keywords"] == ["SkyGuard", "Juggernaught"]
    assert card["int_attributes"] == {"Deploy": 1, "Lethal": 1}
    fields = {"name": "Dire Toad", "rarity": "Uncommon", "card_type": "Troop", "cost": 4, "attack": 3,
              "defense": 4, "subtype": "Beast", "threshold": [{"shard": "Wild", "count": 1}]}
    saved = cards.save_card(db, DIRE_TOAD, dict(fields, keywords=["Flight", "SkyGuard", "Juggernaught"],
                                                int_attributes={"Deploy": 1, "Lethal": 1, "Rage": "2"}))
    assert saved["card"]["keywords"] == ["Flight", "SkyGuard", "Juggernaught"]
    row = campaign_data.load_card_overlay()[DIRE_TOAD]
    assert row["int_attributes"] == {"Deploy": 1, "Lethal": 1, "Rage": 2} and row["name"] == "Dire Toad"
    # A save without them keeps them; a save of the Records values drops them from the row.
    cards.save_card(db, DIRE_TOAD, fields)
    assert campaign_data.load_card_overlay()[DIRE_TOAD]["keywords"][0] == "Flight"
    cards.save_card(db, DIRE_TOAD, dict(fields, keywords=card["keywords"], int_attributes=card["int_attributes"]))
    assert not {"keywords", "int_attributes"} & set(campaign_data.load_card_overlay()[DIRE_TOAD])
    for bad in ({"keywords": ["Flying"]}, {"int_attributes": {"Lethality": 1}}, {"int_attributes": {"Lethal": "x"}}):
        try:
            cards.save_card(db, DIRE_TOAD, dict(fields, **bad))
            raise AssertionError(f"{bad} was saved")
        except ValueError:
            pass

    # A custom card stores them in its m_ fields; its other fields stay as they were.
    records = json.loads((work / "CardTemplates.json").read_text(encoding="utf-8"))
    before = records[0]
    custom = cards.get_card(db, before["m_Id"]["m_Guid"])["card"]
    cards.save_card(db, custom["guid"], dict({k: custom[k] for k in fields}, set_guid=custom["set_guid"],
                                             keywords=["FirstStrike"], int_attributes={"Lethal": 1}))
    after = json.loads((work / "CardTemplates.json").read_text(encoding="utf-8"))[0]
    assert after["m_AttributeFlags"] == "FirstStrike"
    assert card_attributes.tac_ints(after["m_SerializedTAC"]["data"]) == {"Lethal": 1}
    assert {k for k in set(after) | set(before) if after.get(k) != before.get(k)} == {
        "m_AttributeFlags", "m_SerializedTAC"}


def test_set_incdata_holds_the_done_cards():
    import gzip
    import card_abilities
    import incdata

    work = Path(tempfile.mkdtemp())
    campaign_data.CARD_TEMPLATES_PATH = work / "card_templates.json"
    campaign_data.ABILITY_RECORDS_PATH = work / "ability_records.json"
    for name in ("CardTemplates.json", "CardSetTemplate.json"):
        (work / name).write_text((cards.CUSTOM_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    cards.CUSTOM_DIR = work
    cards.CUSTOM_CARDS_PATH = work / "CardTemplates.json"
    cards.CUSTOM_SETS_PATH = work / "CardSetTemplate.json"
    cards._CACHE.clear()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE card_templates (guid TEXT, set_guid TEXT, name TEXT, rarity TEXT, "
               "cost INTEGER, attack INTEGER, defense INTEGER, card_type TEXT, subtype TEXT, "
               "threshold_json TEXT, abilities_json TEXT, variable_cost INTEGER, "
               "socket_count INTEGER, is_pve INTEGER)")
    custom_set = next(entry for entry in cards.card_sets() if entry["source"] == "custom")
    first = json.loads((work / "CardTemplates.json").read_text(encoding="utf-8"))[0]
    card = cards.get_card(db, first["m_Id"]["m_Guid"])["card"]
    # A custom card starts at the client's first stage; only a Done one is built.
    assert card["impl_stage"] == cards.impl_stages()[0]["name"] != cards.IMPL_DONE
    # A new ability, built from sentences, linked to the card.
    ability = __import__("ability_dsl").save_text("When this enters play, draw a card.")["guid"]
    fields = {k: card[k] for k in ("name", "set_guid", "rarity", "cost", "attack", "defense",
                                   "card_type", "subtype", "threshold", "variable_cost")}
    cards.save_card(db, card["guid"], dict(fields, impl_stage="Done", abilities=[ability],
                                           int_attributes={"Lethal": 1}))
    built = incdata.build(db, custom_set["guid"])
    sections = incdata.unpack(built["data"])
    assert gzip.decompress(built["data"]).decode().startswith("$$$---$$$\nCardSetTemplate\n")
    assert [c["m_Name"] for c in sections["CardTemplate"]] == [card["name"]]
    written = sections["CardTemplate"][0]
    assert written["m_ImplStage"] == "Done" and written["_v"][0] == {"CardTemplate": 77}
    assert written["m_CardAbilities"][0]["m_CardAbilityId"]["m_Guid"] == ability
    assert written["m_DefaultLayout"]["m_PortraitLeft"] == incdata.LAYOUT["m_PortraitLeft"]
    assert [a["m_AbilityTemplateId"]["m_Guid"] for a in sections["AbilityTemplate"]] == [ability]
    assert built["summary"]["skipped_not_done"]   # the set's other cards are not Done
    # Every record reads back with the project's Records reader.
    from gamedata.records import deserialize_line
    for records in sections.values():
        for record in records:
            assert deserialize_line(json.dumps(incdata.tdf(record))).type_name == record["_t"]
    assert card_abilities.RECORDS.exists()


ROOT_ABILITY = "2d2687e4-a72b-4c34-9ef6-fce95c42ab17"   # Records "Rage X Ability": one effect, one target


def test_ability_checks_and_overlay_round_trip():
    import card_abilities

    campaign_data.ABILITY_RECORDS_PATH = Path(tempfile.mkdtemp()) / "ability_records.json"
    original = card_abilities.get_ability(ROOT_ABILITY)["record"]

    def rejected(change):
        record = copy.deepcopy(original)
        change(record)
        try:
            card_abilities.save_records([{"section": "AbilityTemplate", "record": record}])
        except card_abilities.Invalid as caught:
            return {(p["path"], p["level"]) for p in caught.problems}
        raise AssertionError("the save was not rejected")

    entry = "m_AbilityEffectList.0"
    assert (f"{entry}.m_TargetTemplateIndex", "error") in rejected(
        lambda r: r["m_AbilityEffectList"][0].update(m_TargetTemplateIndex=3))
    assert (f"{entry}.m_EffectDuration", "error") in rejected(
        lambda r: r["m_AbilityEffectList"][0].update(m_EffectDuration="Forever"))
    assert (f"{entry}.m_EffectTemplateId", "error") in rejected(
        lambda r: r["m_AbilityEffectList"][0].update(m_EffectTemplateId={"m_Guid": BASE_GUID}))
    assert ("m_ActivationCost", "error") in rejected(lambda r: r.update(m_ActivationCost=-1))

    def contingent_loop(record):
        second = dict(record["m_AbilityEffectList"][0], m_EffectInstanceId=1, m_ContingentEffectInstanceId=0)
        record["m_AbilityEffectList"][0]["m_ContingentEffectInstanceId"] = 1
        record["m_AbilityEffectList"].append(second)
    assert any("ContingentEffectInstanceId" in path for path, _ in rejected(contingent_loop))
    assert not campaign_data.ABILITY_RECORDS_PATH.exists()   # nothing rejected was written

    # A valid edit shadows the Records record; a new effect is saved with the root that uses it.
    effect = card_abilities.new_record(
        "AbilityEffectTemplate", "Game.Shared.Mechanics.Abilities.DrawCardAbilityEffectTemplate")
    edited = copy.deepcopy(original)
    edited["m_ActivationCost"] = 1
    edited["m_AbilityEffectList"].append(dict(
        edited["m_AbilityEffectList"][0], m_EffectInstanceId=1, m_EffectTemplateId={"m_Guid": effect["guid"]}))
    card_abilities.save_records([{"section": "AbilityTemplate", "record": edited},
                                 {"section": "AbilityEffectTemplate", "record": effect["record"]}])
    reread = card_abilities.get_ability(ROOT_ABILITY)
    assert reread["source"] == "edited" and reread["record"]["m_ActivationCost"] == 1
    assert reread["children"][effect["guid"]]["source"] == "new"
    assert cards._ability_links([ROOT_ABILITY])[0]["guid"] == ROOT_ABILITY
    try:
        cards._ability_links([BASE_GUID])
        raise AssertionError("an unknown ability was linked")
    except ValueError as error:
        assert "no ability" in str(error)

    # Saving the Records version back drops the shadow.
    card_abilities.save_records([{"section": "AbilityTemplate", "record": original}])
    assert card_abilities.get_ability(ROOT_ABILITY)["source"] == "records"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
