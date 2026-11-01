import { loadPyodide } from "pyodide";
import { readFileSync } from "node:fs";
const REPO = "/home/ianutley/Hex";
const GAMEDATA = "/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE/Data/gamedata";
const py = await loadPyodide();
// Repo mounted read-only in intent: the probe only imports from it.
await py.loadPackage("pyyaml");
py.FS.mkdirTree("/repo");
py.FS.mount(py.FS.filesystems.NODEFS, { root: REPO }, "/repo");
py.FS.mkdirTree("/data");
py.FS.writeFile("/data/gamedata", readFileSync(GAMEDATA));
const t0 = Date.now();
try {
  py.runPython(`
import os, sys, time
sys.dont_write_bytecode = True
os.environ["HEX_DB_PATH"] = "/data/hex.db"   # never the real hconnect.db
os.environ["HEX_GAMEDATA"] = "/data/gamedata"
sys.path[:0] = ["/repo", "/repo/AssetExtraction"]
import sqlite3
import static
db = sqlite3.connect("/data/hex.db")
t = time.time()
static.ensure_schema(db)
db.commit()
print(f"ensure_schema took {time.time() - t:.1f}s")
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables}
print(f"tables={len(tables)} rows={sum(counts.values())}")
for name in sorted(counts, key=lambda n: -counts[n])[:8]:
    print(f"  {name}: {counts[name]}")
`);
} catch (err) {
  console.log("PY ERROR:", String(err).split("\n").slice(0, 3).join(" | "));
}
console.log("wall", Date.now() - t0, "ms");
const bytes = py.FS.readFile("/data/hex.db");
import("node:fs").then((fs) => fs.writeFileSync("/home/ianutley/Hex/browser_demo/probe/out.db", bytes));
