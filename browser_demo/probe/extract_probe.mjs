import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
const GAMEDATA = "/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE/Data/gamedata";
const SRC = "/home/ianutley/Hex/AssetExtraction";
const py = await loadPyodide();
py.FS.mkdirTree("/data");
py.FS.writeFile("/data/gamedata", readFileSync(GAMEDATA));
// Copy the extractor package (read-only use of the repo files) into the sandbox.
function copyTree(src, dst) {
  py.FS.mkdirTree(dst);
  for (const name of readdirSync(src)) {
    if (name === "__pycache__") continue;
    const from = join(src, name), to = join(dst, name);
    if (statSync(from).isDirectory()) copyTree(from, to);
    else if (name.endsWith(".py")) py.FS.writeFile(to, readFileSync(from, "utf8"));
  }
}
copyTree(SRC, "/seedlib/AssetExtraction");
const t0 = Date.now();
const out = py.runPython(`
import sys, time
sys.path[:0] = ["/seedlib", "/seedlib/AssetExtraction"]
import gamedata_seed as gs
t = time.time()
seed = gs.extract("/data/gamedata")
elapsed = time.time() - t
def size(v):
    return len(v) if hasattr(v, "__len__") else 0
tables = seed.get("tables", seed) if isinstance(seed, dict) else {}
rows = {k: size(v) for k, v in tables.items()}
lines = [f"extract took {elapsed:.1f}s; tables={len(rows)} rows={sum(rows.values())}"]
lines += [f"  {k}: {v}" for k, v in sorted(rows.items(), key=lambda kv: -kv[1])[:10]]
"\\n".join(lines)
`);
console.log(out);
console.log("wall", Date.now() - t0, "ms");
