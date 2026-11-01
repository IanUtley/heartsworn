"""List the threads a full match asks for (recorded by shim.NoThread)."""
import sys
sys.path[:0] = ["/repo", "/browser_demo/engine"]
import shim  # noqa: F401  (before the engine imports)
import fra_from_gamedata
fra_from_gamedata.populate("/data/gamedata", "/data/hex.db")
import web_client.match as wm

decks = wm.deck_choices()
m = wm.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"], seed=1, ai_delay=0)
with wm.ENGINE_LOCK:
    m._setup()
m._publish()
m._advance()
script = {"goes_first": {"type": "play_first"},
          "mulligan": {"type": "keep"}, "keep": {"type": "keep"}}
for _ in range(400):
    if m.ended:
        break
    p = m.prompt() or {}
    kind = p.get("kind")
    if kind in ("waiting", "ended", "stalled"):
        break
    try:
        m._apply(script.get(kind, {"type": "pass"}))
        m._advance()
    except Exception as exc:
        print("step error", type(exc).__name__, str(exc)[:160])
        break
print("threads requested:", shim.NoThread.started)
print("ended:", m.ended, "error:", (m.error or "")[:120])
