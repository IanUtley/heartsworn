"""The ability sentences (campaign_editor/ability_dsl.py): Records abilities read back
into sentences compile to the same records, and a compiled ability is saved to the
ability overlay with its new effect, condition and target. Records is never written;
the overlay is a scratch file."""

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign"), os.path.join(ROOT, "campaign_editor")]

import campaign_data  # noqa: E402

campaign_data.ABILITY_RECORDS_PATH = Path(tempfile.mkdtemp()) / "ability_records.json"

import ability_dsl as dsl  # noqa: E402
import card_abilities  # noqa: E402

BURN = "a844c02c-017e-bdcf-7622-700b6d01cade"   # Records "Deal 2 damage to target champion or troop."


def test_records_abilities_round_trip():
    # Every 15th Records ability: each one the sentences can say must compile back to
    # the same records (compared by content, presentation fields and GUIDs aside).
    sample = sorted(card_abilities._records_index("AbilityTemplate"))[::15]
    result = dsl.coverage(sample)
    assert not result["inexact"], [dsl.decompile(g)["reasons"] for g in result["inexact"][:3]]
    assert len(result["exact"]) >= 0.5 * len(sample), (len(result["exact"]), len(sample))
    # The built target grammar agrees with every phrase Records has for it.
    assert dsl.built_target_mismatches() == []

    read = dsl.decompile(BURN)
    assert read["exact"] and read["text"] == "Deal 2 damage to target champion or troop."
    compiled = dsl.compile_text("When this card enters play, deal 2 damage to target opposing troop.")
    # Game text names this trigger by its keyword; the renderer writes that form.
    assert compiled["text"] == "Deploy - deal 2 damage to target opposing troop."
    root = compiled["root"]
    assert root["m_TriggerEventType"]["m_InternalType"].endswith("CardEnteredZoneEvent")
    assert root["m_Variables"][0]["m_DefaultValue"] == 2 and not compiled["new"]


def test_compiled_ability_saves_to_the_overlay():
    records_file = card_abilities.RECORDS / "AbilityTemplate.jsonl"
    stamp = records_file.stat().st_mtime_ns
    cat = dsl.catalogue()
    # A card Records never summons needs a new summon effect; the built target and
    # the threshold condition are new records too.
    card = next(guid for guid, name in sorted(cat["cards"]["name"].items())
                if name.isalpha() and guid not in cat["cards"]["uses"]
                and cat["cards"]["by_name"][name.lower()] == [guid])
    name = cat["cards"]["name"][card]
    text = (f'When this enters play, summon a "{name}" if you have 7 wild threshold.\n'
            "Then destroy each opposing constant.")
    saved = dsl.save_text(text)
    assert sorted(entry["section"] for entry in saved["new"]) == [
        "AbilityEffectConditionTemplate", "AbilityEffectTemplate", "AbilityTargetTemplate"]
    overlay = campaign_data.load_ability_overlay()
    assert saved["guid"] in overlay["AbilityTemplate"]
    assert all(entry["guid"] in overlay[entry["section"]] for entry in saved["new"])
    reread = dsl.decompile(saved["guid"])
    # The saved ability reads back as rows where its targets can be words, and those words
    # read back to themselves.
    assert reread["exact"] and dsl.render(dsl.parse(reread["text"])) == reread["text"]

    # Editing a Records ability shadows it; saving its own sentences again drops the shadow.
    dsl.save_text("Deal 3 damage to target champion or troop.", BURN)
    assert card_abilities.get_ability(BURN)["source"] == "edited"
    assert dsl.decompile(BURN)["text"] == "Deal 3 damage to target champion or troop."
    dsl.save_text("Deal 2 damage to target champion or troop.", BURN)
    assert card_abilities.get_ability(BURN)["source"] == "records"

    try:
        dsl.compile_text("Deal 2 damage to target dragon.")
        raise AssertionError("an unknown target compiled")
    except ValueError as error:
        assert "line 1" in str(error) and "target dragon" in str(error)
    assert records_file.stat().st_mtime_ns == stamp


def test_builder_offers_only_sentences_that_round_trip():
    # Every template compiles and round-trips exactly, and none is dropped for failing.
    assert dsl.template_problems() == {}
    built = dsl.builder()
    assert len(built["templates"]) == len(dsl.TEMPLATES)

    def sentence(trigger, clause):
        return dsl.render({"trigger": trigger, "clauses": [dsl._clause(**clause)]})

    # For a few shapes, what each step offers round-trips, against the other parts at
    # their sample: a spread of the targets, every duration and enumerated value.
    for shape_id in ("damage", "draw", "keyword", "summon", "threshold", "set_attribute", "fire_event"):
        shape = next(s for s in built["shapes"] if s["id"] == shape_id)
        sample = dsl._sample(shape_id)
        targets = [row["phrase"] for row in dsl.builder_targets(shape_id)]
        offered = [dict(sample, target=t) for t in targets[:3] + targets[3::150]]
        offered += [dict(sample, duration=d) for d in shape["durations"]]
        for slot, values in (("keyword", built["keywords"]), ("shard", built["shards"]),
                             ("attribute", built["attributes"][::10]), ("event", built["events"])):
            if slot in sample:
                offered += [dict(sample, **{slot: v}) for v in values]
        for clause in offered:
            assert dsl.round_trip(sentence("", clause)) == [], sentence("", clause)
    draw = dsl._sample("draw")
    for row in built["triggers"][::40]:
        assert dsl.round_trip(sentence(row["phrase"], draw)) == [], row
    for row in built["conditions"][::40]:
        assert dsl.round_trip(sentence("", dict(draw, condition=row["phrase"]))) == [], row
    for row in built["counts"][::10]:
        assert dsl.round_trip(sentence("", dict(draw, amount="a", count=row["phrase"]))) == [], row
    # A value the client keeps at its default compiles but would not read back: not offered.
    assert "Unknown" not in built["keywords"] and dsl.round_trip("Target troop gets Unknown.")

    # Text that does not parse opens in the builder at the gap.
    read = dsl.read_text("When this enters play, deal 2 damage to target dragon.")
    assert not read["ok"] and "target dragon" in read["error"]
    clause = read["partial"]["clauses"][0]
    assert read["partial"]["trigger"] == "when this enters play"
    assert clause["shape"] == "damage" and clause["target"] is None and clause["amount_value"] == 2


def test_replacement_sentences_round_trip():
    # Records' replacement roots ("would" triggers): none is read without being
    # reproduced, and at least the 33 known ones round-trip.
    roots = [campaign_data.ability_record_guid("AbilityTemplate", root)
             for root in card_abilities._iter_records("AbilityTemplate")
             if dsl.REPLACEMENT_EVENT.search(str((root.get("m_TriggerEventType") or {}).get("m_InternalType")))]
    result = dsl.coverage(roots)
    assert len(roots) == 38 and not result["inexact"] and len(result["exact"]) >= 33, len(result["exact"])
    assert dsl.decompile("1a5c43ec-2c65-85f0-0310-8395ec405acf")["text"] == (
        "If this would enter a hand or crypt, void this.\nThen reveal this (keeping targets).\n"
        "Then deal 4 damage to you instead.")

    # The trigger's unsaid root values come with it: the chain is ignored, and a card's
    # own "would enter a zone" works from every zone. "instead" is optional on input.
    compiled = dsl.compile_text("If this would die, void this.")
    root = compiled["root"]
    assert compiled["text"] == "If this would die, void this instead."
    assert root["m_TriggerEventType"]["m_InternalType"].endswith("CardWouldEnterZoneEvent")
    assert root["m_IgnoresChain"] == 1 and "Deck" in root["m_TriggerCollectionFlags"]
    assert dsl.round_trip(compiled["text"]) == []
    for text in ("Void this instead.", "If this would die, void this instead.\nThen draw a card."):
        try:
            dsl.compile_text(text)
            raise AssertionError(f"{text!r} compiled")
        except ValueError as error:
            assert "instead" in str(error)

    # An on/off card value reads "gets"; a keyword of the same name stays a keyword.
    assert dsl.round_trip("Target troop gets Lethal this turn.") == []
    assert dsl.compile_text("Target troop gets Rage.")["parsed"]["clauses"][0]["shape"] == "keyword"
    # A keyword may be written by its display name (ToolName); it reads back by member name.
    assert dsl.compile_text("Target troop gets Crush and Swift Strike.")["text"] == \
        "Target troop gets Juggernaught and FirstStrike."
    built = dsl.builder()
    assert {row["phrase"] for row in built["triggers"] if row["replacement"]} >= {
        "if this would die", "if damage would be dealt to this", "if you would lose the game"}
    assert "Lethal" in next(s for s in built["shapes"] if s["id"] == "gets_flag")["values"]["attribute"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
