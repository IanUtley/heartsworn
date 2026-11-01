import { loadPyodide } from "pyodide";
const t0 = Date.now();
const py = await loadPyodide();
console.log("pyodide load", Date.now() - t0, "ms");
py.FS.mkdirTree("/repo");
py.FS.mount(py.FS.filesystems.NODEFS, { root: "/home/ianutley/Hex" }, "/repo");
console.log(py.runPython(`
import sys, time, resource
sys.path[:0] = ["/repo"]
t = time.time()
from gamedata import DEFAULT_RECORD_STORE
imported = time.time() - t
lines = [f"gamedata import {imported:.1f}s"]
for s in ("CardTemplate", "AbilityTemplate", "AbilityTargetTemplate", "AbilityEffectTemplate"):
    t = time.time(); recs = DEFAULT_RECORD_STORE.load(s)
    lines.append(f"{s}: {len(recs)} {time.time()-t:.1f}s")
lines.append(f"rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024} MB")
"\\n".join(lines)
`));
console.log("total", Date.now() - t0, "ms");
