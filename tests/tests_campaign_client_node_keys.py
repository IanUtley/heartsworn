"""A dungeon node the client looks up by its Records name keeps its text.

The client finds a map node's title and descriptions by the ``node`` name the
server sends, so Fort Romor's Command Post (rules ``NodeD``) is sent as
``CmdPost``, the key its Records item is stored under. The map still binds by
the GameObject id ``NodeD``, and the server still resolves ``CmdPost`` back.
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "campaign")]

from campaign_runtime import catalog_data, projection  # noqa: E402


def test_command_post_is_sent_under_its_records_key():
    keys = catalog_data.client_node_keys("Fort Romor")
    assert keys["NodeD"] == "CmdPost", keys

    with open(os.path.join(ROOT, "campaign", "campaign_rules", "fortromor.json"),
              encoding="utf-8") as handle:
        rules = json.load(handle)
    state = {"flags": [], "counters": {}, "current": "Node001",
             "nodes": {"Node001": {"completed": True, "visits": 1}}}
    entry = {"id": "fortromor", "kind": "dungeon", "records": "Fort Romor"}
    out = projection.project(rules, state, entry, None, children={}, active=None,
                             client_keys=keys)

    post = next(v["Data"] for v in out["VisLocs"] if v["Data"]["rules_node"] == "NodeD")
    assert post["node"] == "CmdPost" and post["name"] == "NodeD", post
    # The map binds a GameObject (Data.id NodeD) to the location whose node is
    # the LocNodes Name, so the LocNodes Name must be the key too.
    assert any(n["Name"] == "CmdPost" and n["Data"]["id"] == "NodeD"
               for n in out["LocNodes"]), out["LocNodes"]


def test_prefab_fork_without_offers_is_projected_as_a_fork():
    import campaign_rules as engine

    rules = {"nodes": {"Node7": {"fork": True}, "NodeB": {"fork": True, "entries": [{"id": "x"}]}}}
    assert engine.is_fork(rules, "Node7")
    # A flagged node with an encounter or conversation of its own stays a stop.
    assert not engine.is_fork(rules, "NodeB")
    assert "Node7" in catalog_data.prefab_fork_nodes("fortromor")


if __name__ == "__main__":
    test_command_post_is_sent_under_its_records_key()
    test_prefab_fork_without_offers_is_projected_as_a_fork()
    print("PASS test_command_post_is_sent_under_its_records_key")
    print("PASS test_prefab_fork_without_offers_is_projected_as_a_fork")
