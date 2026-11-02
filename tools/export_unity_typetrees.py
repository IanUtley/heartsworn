#!/usr/bin/env python3
"""Export the Unity type trees the browser needs to read the background prefabs in resources.assets.

resources.assets stores its objects without type trees, so the browser cannot decode them from the file. This writes the
engine's own layouts for the few classes the layered backgrounds use (from UnityPy's packed type-tree set, for the
file's Unity version) to web_client/static/unity/typetrees.json. The layouts describe fields only; no game assets.

Usage (from the repo root):
    python3 tools/export_unity_typetrees.py "<HEX SHARDS OF FATE>/Hex_Data/resources.assets"
"""

import json
import os
import sys

import UnityPy
from UnityPy.helpers import Tpk

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web_client", "static", "unity", "typetrees.json")

# Unity class IDs: GameObject, Transform, Material, MeshRenderer, Texture2D, MeshFilter, Mesh, ParticleSystem,
# ParticleSystemRenderer.
CLASSES = [1, 4, 21, 23, 28, 33, 43, 198, 199]


def nested(node):
    """A UnityPy type-tree node in the browser's shape: { type, name, level, meta, children }."""
    return {"type": node.m_Type, "name": node.m_Name, "level": node.m_Level, "meta": node.m_MetaFlag,
            "children": [nested(child) for child in node.m_Children]}


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    env = UnityPy.load(argv[1])
    version = next(iter(env.files.values())).version
    trees = {str(class_id): nested(Tpk.get_typetree_node(class_id, version)) for class_id in CLASSES}
    with open(OUT, "w", encoding="utf-8") as stream:
        json.dump({"unity": str(version), "classes": trees}, stream, indent=1)
    print(f"wrote {os.path.normpath(OUT)} for Unity {version}: {len(trees)} classes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
