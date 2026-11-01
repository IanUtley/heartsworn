"""Browser-side shims for running the web match in Pyodide (no threads there).

Nothing in the repo is changed: the engine's own code runs as written, with
threads replaced by a recorder and the match worker's loop driven by
``drive_*`` helpers below.
"""
import os
import sys
import threading

os.environ.setdefault("HEX_DB_PATH", "/data/hex.db")
os.environ.setdefault("HEX_GAMEDATA", "/data/gamedata")
sys.dont_write_bytecode = True


class NoThread:
    """Stands in for threading.Thread: records the target, never starts it."""
    started = []

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None, **_):
        self._target, self._args, self._kwargs = target, args, kwargs or {}
        self.name = name

    def start(self):
        NoThread.started.append(self.name)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


threading.Thread = NoThread
