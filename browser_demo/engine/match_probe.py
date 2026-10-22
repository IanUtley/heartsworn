import sys, traceback
sys.path[:0] = ["/repo", "/browser_demo/engine"]
import shim  # noqa: F401  (must come before the engine imports)
try:
    import web_client.match as wm
    print("imported web_client.match")
    decks = wm.deck_choices()
    print("deck choices:", len(decks))
except Exception:
    traceback.print_exc()
