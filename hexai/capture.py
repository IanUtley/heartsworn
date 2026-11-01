"""Opt-in decision capture for headless games.

Enabled with ``HEXAI_CAPTURE_DIR``.  ``pvp_autoplay.py`` calls ``begin_game`` /
``record_decision`` / ``end_game``; everything else in the server is unchanged
when the environment variable is unset.

The writer is append-only JSONL, one shard per process.  Records are flushed per
decision so a killed simulation still yields usable data; ``train`` reads every
``*.jsonl`` (and ``*.jsonl.gz``) shard in the directory.

Thread safety: ``pvp_autoplay`` runs games in a thread pool.  A single lock
guards the shard handle and the in-flight game metadata map, and each record is
written as one line.
"""

from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()
_TLS = threading.local()
_WRITER = None
_PATH = None
_GAMES: dict[str, dict] = {}
_ENABLED = False
_INIT_ERROR = None


def current_game():
    """Game id for the calling thread (set by ``begin_game``)."""
    return getattr(_TLS, "game", None)


def enabled() -> bool:
    return _ENABLED


def _open_writer():
    global _WRITER, _PATH, _ENABLED, _INIT_ERROR
    directory = os.environ.get("HEXAI_CAPTURE_DIR")
    if not directory:
        return
    try:
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        _PATH = os.path.join(
            directory, f"shard-{os.getpid()}-{stamp}.jsonl")
        _WRITER = open(_PATH, "w", encoding="utf-8")
        _ENABLED = True
    except OSError as exc:  # pragma: no cover - filesystem failure only
        _INIT_ERROR = str(exc)
        _ENABLED = False


def path() -> str | None:
    return _PATH


def init() -> bool:
    """Open the shard if capture is requested; idempotent."""
    if _WRITER is None and _INIT_ERROR is None:
        _open_writer()
    return _ENABLED


def _write(record: dict) -> None:
    if not _ENABLED:
        return
    with _LOCK:
        try:
            _WRITER.write(json.dumps(record, separators=(",", ":")) + "\n")
            _WRITER.flush()
        except (OSError, ValueError):
            pass


def begin_game(game_id, seed, pids, decks=None) -> None:
    """Record the decks and seats for one game."""
    if not init():
        return
    game_id = str(game_id)
    _TLS.game = game_id
    _TLS.last_signature = None
    with _LOCK:
        _GAMES[game_id] = {"pids": [int(pid) for pid in pids],
                           "seed": seed, "ended": False}
    _write({
        "type": "game",
        "game": game_id,
        "seed": seed,
        "pids": [int(pid) for pid in pids],
        "decks": decks or {},
        "started_at": time.time(),
    })


def record_decision(*, game_id=None, seat_pid, opponent_pid, turn, kind,
                    feature_ids, candidates, chosen, value_seat=None,
                    meta=None) -> None:
    """Append one decision sample."""
    if not _ENABLED:
        return
    if game_id is None:
        game_id = current_game()
    if game_id is None:
        return
    features = list(feature_ids or ())
    candidate_list = list(candidates or ())
    signature = (str(game_id), int(seat_pid), int(turn or 0), str(kind),
                 str(chosen), tuple(features),
                 tuple(str(c.get("key")) for c in candidate_list))
    if getattr(_TLS, "last_signature", None) == signature:
        return
    _TLS.last_signature = signature
    _write({
        "type": "decision",
        "game": str(game_id),
        "seat": int(seat_pid),
        "opp": int(opponent_pid),
        "turn": int(turn or 0),
        "kind": str(kind),
        "features": features,
        "candidates": candidate_list,
        "chosen": str(chosen),
        "value_seat": int(seat_pid if value_seat is None else value_seat),
        "meta": meta or {},
    })


def end_game(game_id=None, winner_pid=None, *, turns=None, health=None) -> None:
    if not _ENABLED:
        return
    if game_id is None:
        game_id = current_game()
    if game_id is None:
        return
    game_id = str(game_id)
    with _LOCK:
        info = _GAMES.setdefault(game_id, {"pids": [], "ended": False})
        if info.get("ended"):
            return
        info["ended"] = True
        pids = list(info.get("pids") or ())
        _GAMES.pop(game_id, None)
    _write({
        "type": "end",
        "game": game_id,
        "winner": None if winner_pid is None else int(winner_pid),
        "pids": pids,
        "turns": turns,
        "health": health or {},
    })


def close() -> None:
    global _WRITER, _ENABLED
    with _LOCK:
        if _WRITER is not None:
            try:
                _WRITER.flush()
                _WRITER.close()
            except OSError:
                pass
        _WRITER = None
        _ENABLED = False


def status() -> dict:
    return {"enabled": _ENABLED, "path": _PATH, "error": _INIT_ERROR}
