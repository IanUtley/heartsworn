import { loadPyodide } from "pyodide";
const py = await loadPyodide();
console.log("pyodide", py.version);
console.log(py.runPython(`
import sys, threading, sqlite3
out = []
lock = threading.RLock()
with lock:
    out.append("RLock ok")
l2 = threading.Lock(); l2.acquire(); l2.release(); out.append("Lock ok")
cond = threading.Condition(); out.append("Condition ok")
out.append(f"shared esc={sys._emscripten_info.shared_memory if hasattr(sys,'_emscripten_info') else '?'}")
t = threading.Thread(target=lambda: None)
try:
    t.start(); out.append("Thread.start ok")
except Exception as exc:
    out.append("Thread.start raises: " + type(exc).__name__ + ": " + str(exc))
try:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        out.append("pool result " + str(ex.submit(lambda: 7).result(timeout=2)))
except Exception as exc:
    out.append("ThreadPool raises: " + type(exc).__name__ + ": " + str(exc)[:80])
out.append("sqlite " + sqlite3.sqlite_version)
"\\n".join(out)
`));
