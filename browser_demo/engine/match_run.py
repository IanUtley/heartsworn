import sys, traceback
sys.path[:0] = ["/repo", "/browser_demo/engine"]
import shim  # noqa: F401  (before the engine imports)
import fra_from_gamedata
fra_from_gamedata.populate("/data/gamedata", "/data/hex.db")
import web_client.match as wm
decks = wm.deck_choices()
print("deck choices:", len(decks))
if len(decks) >= 2:
    try:
        m = wm.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"], seed=1, ai_delay=0)
        print("match created, slot", m.slot)
        with wm.ENGINE_LOCK:
            m._setup()
        m._publish()
        m._advance()
        print("info:", m.info())
        print("prompt:", m.prompt())
    except Exception:
        traceback.print_exc()
