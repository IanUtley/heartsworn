import { loadPyodide } from "pyodide";
import { readFileSync, writeFileSync } from "node:fs";
const ENGINE = "/home/ianutley/Hex/browser_demo/engine";
const GAMEDATA = "/mnt/d/SteamLibrary/steamapps/common/HEX SHARDS OF FATE/Data/gamedata";
const py = await loadPyodide();
await py.loadPackage("pyyaml");
py.FS.mkdirTree("/repo"); py.FS.mount(py.FS.filesystems.NODEFS, { root: "/home/ianutley/Hex" }, "/repo");
py.FS.mkdirTree("/browser_demo/engine"); 
for (const f of ["shim.py", "match_probe.py", "match_run.py", "fra_from_gamedata.py"]) py.FS.writeFile(`/browser_demo/engine/${f}`, readFileSync(`${ENGINE}/${f}`, "utf8"));
py.FS.mkdirTree("/data");
py.FS.writeFile("/data/hex.db", readFileSync("/home/ianutley/Hex/browser_demo/probe/out.db"));
py.FS.writeFile("/tmp/.keep", "");
py.FS.writeFile("/data/gamedata", readFileSync(GAMEDATA));
const file = process.argv[2] || "match_probe.py";
const code = readFileSync(`${ENGINE}/${file}`, "utf8");
try {
  py.runPython(code.replace(/^/, "") , { filename: `/browser_demo/engine/${file}` });
} catch (err) {
  console.log("PY ERROR:", String(err).split("\n").slice(-6).join("\n"));
}
