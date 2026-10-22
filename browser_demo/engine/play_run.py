import sys, traceback, time
sys.path[:0] = ["/repo", "/browser_demo/engine"]
import shim  # noqa: F401
import fra_from_gamedata
fra_from_gamedata.populate("/data/gamedata", "/data/hex.db")
import web_client.match as wm
decks = wm.deck_choices()
m = wm.WebMatch(decks[0]["deck_guid"], decks[1]["deck_guid"], seed=1, ai_delay=0)
with wm.ENGINE_LOCK:
    m._setup()
m._publish()
m._advance()

def step(action):
    """Apply one action the way the worker thread would, then let the AI play."""
    with m.cond:
        m.busy = True
    try:
        m.error = None
        m._apply(action)
        m._advance()
    except wm.ActionError as exc:
        m.error = str(exc)
    finally:
        with m.cond:
            m.busy = False

script = {"goes_first": {"type": "play_first"},
          "mulligan": {"type": "keep"}, "keep": {"type": "keep"}}
turns_seen = set()
for i in range(400):
    if m.ended:
        print("match ended after", i, "steps")
        break
    p = m.prompt() or {}
    kind = p.get("kind")
    turns_seen.add(p.get("turn_number"))
    if i < 6 or kind not in ("pass",):
        print(f"[{i}] prompt={p}")
    action = script.get(kind, {"type": "pass"})
    before = m.error
    step(action)
    if m.error:
        print(f"[{i}] action {action} refused: {m.error[:120]}")
        if action.get("type") == "pass":
            step({"type": "attack"} if False else {"type": "defend", "blocks": []})
print("turns reached:", sorted(t for t in turns_seen if t is not None))
print("final prompt:", m.prompt())
print("info:", {k: v for k, v in m.info().items() if k != "decks"})
