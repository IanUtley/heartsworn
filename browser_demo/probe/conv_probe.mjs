import { loadPyodide } from "pyodide";
import { readFileSync } from "node:fs";
const py = await loadPyodide();
await py.loadPackage("pyyaml");
py.FS.mkdirTree("/repo"); py.FS.mount(py.FS.filesystems.NODEFS, { root: "/home/ianutley/Hex" }, "/repo");
py.FS.mkdirTree("/data");
py.FS.writeFile("/data/gamedata", readFileSync("/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE/Data/gamedata"));
py.runPython(`
import os, sys
sys.dont_write_bytecode = True
os.environ["HEX_GAMEDATA"] = "/data/gamedata"
sys.path[:0] = ["/repo/AssetExtraction", "/repo"]
import gamedata_seed as gs
text = gs.load_text("/data/gamedata")
print("text chars", len(text))
recs = gs.records(text, "ConversationTemplate")[0]
print("ConversationTemplate records", len(recs))
print("az rows", len(gs.extract_campaign_node_conversations()))
`);
