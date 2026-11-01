"""Every dungeon zone names its own client prefabs and DungeonTemplate.

campaign._build_camp_summary falls back to Castle Crayburn's prefabs and
template when a dungeon zone in zones.json lacks ``prefab_root`` /
``campaign_template_id``.  The client then builds the map from Crayburn's
nodes prefab, none of which match the zone's VisLocs, so no player token
start node is set and the dungeon renders black (seen on The Smoldering Dead).
"""

import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ZONES = os.path.join(ROOT, "campaign", "campaign_rules", "zones.json")
SCENES = os.path.join(ROOT, "hex-private", "Records", "SceneData.jsonl")


def _dungeon_templates():
    out = {}
    with open(SCENES, encoding="utf-8") as fh:
        for line in fh:
            text = json.loads(line)
            if "Reckoning.Game.DungeonTemplate" not in text[:400]:
                continue
            m = re.search(r'"m_Guid" : "([0-9a-f-]+)"\},\s*"m_Name" : "([^"]+)"', text)
            if m:
                out[m.group(2)] = m.group(1)
    return out


def test_dungeon_zones_carry_own_prefab_root_and_template():
    templates = _dungeon_templates()
    zones = json.load(open(ZONES, encoding="utf-8"))["zones"]
    dungeons = [z for z in zones if z.get("kind") == "dungeon"]
    assert any(z["id"] == "smolderingdead" for z in dungeons)
    for zone in dungeons:
        assert zone.get("prefab_root"), zone["id"]
        assert zone.get("campaign_template_id") == templates[zone["records"]], zone["id"]


if __name__ == "__main__":
    test_dungeon_zones_carry_own_prefab_root_and_template()
    print("ok")
