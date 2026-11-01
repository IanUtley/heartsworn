import { loadPyodide } from "pyodide";
const t0 = Date.now();
const py = await loadPyodide();
console.log("loaded in", Date.now() - t0, "ms");
console.log(py.runPython(`
import sys, sqlite3, json
f"python {sys.version.split()[0]}, sqlite {sqlite3.sqlite_version}, threads={__import__('threading').active_count()}"
`));
