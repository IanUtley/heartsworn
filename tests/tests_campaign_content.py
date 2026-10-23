"""campaign_content builds the campaign tables from Records and the data files alone."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign")]

import campaign_content  # noqa: E402


def test_campaign_tables_come_from_records_and_the_data_files():
    scenes = campaign_content.table("encounter_scenes")
    assert len(scenes) == 336
    # an editor value from campaign_data/encounter_scenes.json wins over the seed
    dryad = campaign_content.scene("879317e1-8b04-486e-a10a-f2d2f1a080bc")
    assert dryad["name"] == "AZ 1 - NODE - 18 - CORRUPT DRYAD", dryad
    assert "end_of_game_rewards" in str(dryad["rewards_json"]), dryad
    assert len(campaign_content.table("campaign_node_edges")) == 354
    assert len(campaign_content.table("quest_templates")) == 32
    # a placed conversation shadows the seeded rows of the same conversation
    rows = campaign_content.table("campaign_node_conversations")
    placed = {(r["campaign_template"], r["conversation_guid"].lower())
              for r in rows if r["origin"] == "data"}
    assert placed, "expected editor placements in the data files"
    shadowed = [r for r in campaign_content.node_conversations()
                if r["origin"] != "data"
                and (r["campaign_template"], r["conversation_guid"].lower()) in placed]
    assert not shadowed, shadowed[:1]
    # a data file that fails validation is skipped whole; a valid one is applied
    import json
    import tempfile
    from pathlib import Path
    import campaign_data
    saved = campaign_data.DATA_DIR
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "good.json").write_text(json.dumps({"schema_version": 1, "tables": {
            "encounter_scenes": [{"guid": "00000000-0000-4000-8000-0000000000aa",
                                  "name": "Test scene", "title": "T"}]}}))
        Path(folder, "bad.json").write_text(json.dumps({"schema_version": 1, "tables": {
            "encounter_scenes": [{"name": "no guid"}]}}))
        campaign_data.DATA_DIR = Path(folder)
        try:
            built = campaign_content.table("encounter_scenes")
        finally:
            campaign_data.DATA_DIR = saved
    assert len(built) == 337, len(built)   # the 336 scenes plus the valid file's one
    assert any(r.get("name") == "Test scene" for r in built)
