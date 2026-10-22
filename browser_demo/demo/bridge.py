"""Browser (Pyodide) entry point: the web client's HTTP API as one function.

``route(method, path, query, body)`` answers ``(status, json_text)`` and matches
``web_client/server.py`` route for route, so the page's fetch shim can send
every ``/api/`` call here. Matches run inline (``threaded=False``): the browser
has no threads, so each action is processed when it is submitted.
"""
import json
import os
import re
import traceback
from urllib.parse import parse_qs

from . import shim  # noqa: F401  (threads replaced before the engine imports)

os.environ.setdefault("HEX_DB_PATH", "/data/hex.db")
os.environ.setdefault("HEX_GAMEDATA", "/data/gamedata")

MAX_MATCHES = 8
MATCHES = {}
ORDER = []
GAMEDATA = "/data/gamedata"
DATABASE = "/data/hex.db"
RECORDS = "/data/records"


def _say(report, text):
    if report is not None:
        report(text)


def _point_records_at_folder():
    """Send the engine's Records lookups to the folder written from the gamedata.

    hex-private is not shipped, so without this the catalog finds no card or
    champion templates on the page.
    """
    if not os.path.exists(GAMEDATA):
        return
    from . import fra_from_gamedata
    from pathlib import Path
    import gamedata
    fra_from_gamedata.write_records(GAMEDATA, RECORDS)
    store = gamedata.DEFAULT_RECORD_STORE
    with store._lock:
        store.records_dir = Path(RECORDS)
        store._cache.clear()
        store._index.clear()
        store._issues.clear()


def open_database():
    """Point the engine at a database built earlier (see ``build_database``).

    Returns how many FRA encounter decks it holds; 0 means the database is
    unusable and must be built again.
    """
    import sqlite3
    os.environ["HEX_GAMEDATA"] = GAMEDATA
    os.environ["HEX_DB_PATH"] = DATABASE
    os.environ.setdefault("HEX_REPO", "/app")
    _point_records_at_folder()
    if not os.path.exists(DATABASE):
        return 0
    connection = sqlite3.connect(DATABASE)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM fra_encounters").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        connection.close()


def build_database(report=None):
    """Build the match database from the player's ``Data/gamedata`` file.

    Nothing is shipped: the schema and client-derived rows come from the
    gamedata the page wrote to ``GAMEDATA`` (``static.ensure_schema``), then the
    FRA encounter decks are populated from the same file, as the desktop
    server does. ``report(text)`` is called at each step so the page can say
    what is happening. Must run before any match is created.
    """
    import sqlite3
    open_database()
    import static
    from . import fra_from_gamedata
    _say(report, "Reading the Hex gamedata...")
    if os.path.exists(DATABASE):
        os.remove(DATABASE)
    _say(report, "Building the card, ability and champion tables (about a minute the first time)...")
    connection = sqlite3.connect(DATABASE, timeout=30)
    try:
        static.ensure_schema(connection)
        connection.commit()
    finally:
        connection.close()
    _say(report, "Filling the encounter decks...")
    fra_from_gamedata.populate(GAMEDATA, DATABASE, records_dir=RECORDS)
    # db.py runs the database in WAL mode, so the new rows are in the -wal file.
    # Fold them into the main file, which is the file that gets saved.
    connection = sqlite3.connect(DATABASE)
    try:
        busy, _log, _done = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    if busy:
        raise RuntimeError("the database was busy and could not be saved")
    if open_database() == 0:
        raise RuntimeError("the encounter decks were not built from the gamedata")
    _say(report, "Database ready.")
    return {"ok": True}


def _decks():
    from web_client import catalog
    from web_client.match import deck_choices
    decks = deck_choices()
    names = catalog.describe({"cards": sorted(
        {d["champion_guid"] for d in decks})})["cards"]
    for deck in decks:
        champ = names.get(deck["champion_guid"].lower()) or {}
        deck["champion"] = champ.get("name", "")
    return {"decks": decks}


def _create(body):
    from web_client.match import WebMatch
    if len(MATCHES) >= MAX_MATCHES:
        MATCHES.pop(ORDER.pop(0)).close()
    match = WebMatch(str(body["deck"]), str(body["opponent_deck"]),
                     player_name=str(body.get("name") or "You")[:32],
                     ai_delay=0, threaded=False)
    match_id = f"m{match.slot}"
    MATCHES[match_id] = match
    ORDER.append(match_id)
    return {"id": match_id}


def _match(match_id):
    match = MATCHES.get(match_id)
    if match is None:
        raise LookupError("no such match")
    return match


def route(method, path, query, body_text):
    """Answer one API call as ``(status, json_text)``."""
    try:
        body = json.loads(body_text or "{}")
        params = parse_qs(query or "")
        if method == "GET" and path == "/api/decks":
            return 200, json.dumps(_decks())
        if method == "POST" and path == "/api/matches":
            return 200, json.dumps(_create(body))
        if method == "POST" and path == "/api/catalog":
            from web_client import catalog
            return 200, json.dumps(catalog.describe(body))
        found = re.fullmatch(r"/api/matches/(m\d+)/action", path)
        if method == "POST" and found:
            from web_client.match import ActionError
            try:
                _match(found.group(1)).submit(body)
            except ActionError as exc:
                return 409, json.dumps({"error": str(exc)})
            return 200, json.dumps({"ok": True})
        found = re.fullmatch(r"/api/matches/(m\d+)", path)
        if found and method == "GET":
            after = int((params.get("after") or ["0"])[0])
            return 200, json.dumps(_match(found.group(1)).snapshot(after, 0))
        if found and method == "DELETE":
            match = MATCHES.pop(found.group(1), None)
            if match is not None:
                ORDER.remove(found.group(1))
                match.close()
            return 200, json.dumps({"dropped": match is not None})
        return 404, json.dumps({"error": "not found"})
    except LookupError as exc:
        return 404, json.dumps({"error": str(exc)})
    except (KeyError, ValueError) as exc:
        return 400, json.dumps({"error": str(exc)})
    except Exception:
        return 500, json.dumps({"error": traceback.format_exc(limit=6)})
