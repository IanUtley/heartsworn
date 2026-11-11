#!/usr/bin/env python3
"""HTTP server for the browser battle client.

    python3 -m web_client.server [--port 8780] [--host 127.0.0.1] [--live-db]

Serves ``web_client/static`` and a JSON API:

* ``GET  /api/decks``                 authored decks a seat can play, and the player's playable saved decks
* ``POST /api/matches``               ``{"deck" | "deck_id", "opponent_deck", "name"}``
* ``GET  /api/matches/<id>?after=N``  event packets after ``N`` plus the prompt
                                      (long-polls for up to ``wait`` seconds)
* ``POST /api/matches/<id>/action``   one browser decision
* ``GET  /api/matches/<id>/capture``  the seed, packets and actions, for replay
* ``DELETE /api/matches/<id>``        drop the match and its session rows
* ``POST /api/catalog``               card/ability descriptions by GUID
* ``GET  /art/<a0000000>.jpg``        card art, when a client install is mounted

By default the server plays on a disposable snapshot of ``hconnect.db`` (the
same SQLite online backup the FRA simulator takes), so browser matches never
write session rows into the live database. ``--live-db`` uses ``HEX_DB_PATH``
or ``hconnect.db`` directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
MAX_MATCHES = 8
CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".json": "application/json"}

_snapshot_dir = None


def use_snapshot_database():
    """Point ``HEX_DB_PATH`` at a private online backup of the server DB."""
    global _snapshot_dir
    source = os.environ.get("HEX_DB_PATH") or str(ROOT / "hconnect.db")
    if not os.path.isfile(source):
        raise SystemExit(f"no server database at {source}; run restart.sh "
                         "or docker bootstrap first")
    _snapshot_dir = tempfile.TemporaryDirectory(prefix="hex-web-client-")
    copy = os.path.join(_snapshot_dir.name, "web.db")
    src = sqlite3.connect(source, timeout=30)
    dst = sqlite3.connect(copy, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.environ["HEX_DB_PATH"] = copy
    # Profile data (decks, collection) is saved to the live file, not to this disposable copy.
    os.environ["HEX_LIVE_DB_PATH"] = os.path.abspath(source)
    return copy


class Matches:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_id = {}

    def create(self, body):
        from web_client.match import WebMatch
        with self.lock:
            if len(self.by_id) >= MAX_MATCHES:
                oldest = next(iter(self.by_id))
                self.by_id.pop(oldest).close()
        if "deck_id" in body:
            # A saved deck, built from the live database so a deck saved after the server started can be played.
            from web_client import profile
            match = WebMatch(None, str(body["opponent_deck"]), player_spec=profile.play_spec(int(body["deck_id"])),
                             player_name=str(body.get("name") or "You")[:32],
                             ai_delay=float(body.get("ai_delay", 0.35)))
        else:
            match = WebMatch(str(body["deck"]), str(body["opponent_deck"]),
                             player_name=str(body.get("name") or "You")[:32],
                             ai_delay=float(body.get("ai_delay", 0.35)))
        match_id = f"m{match.slot}"
        with self.lock:
            self.by_id[match_id] = match
        return match_id

    def get(self, match_id):
        with self.lock:
            return self.by_id.get(match_id)

    def drop(self, match_id):
        with self.lock:
            match = self.by_id.pop(match_id, None)
        if match is not None:
            match.close()
        return match is not None


MATCHES = Matches()


class Handler(BaseHTTPRequestHandler):
    server_version = "HeartswornWeb/0.1"

    def log_message(self, fmt, *args):
        if os.environ.get("HEX_WEB_ACCESS_LOG"):
            super().log_message(fmt, *args)

    # -- helpers -------------------------------------------------------

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1 << 20:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _file(self, base: Path, relative: str):
        path = (base / relative).resolve()
        if base.resolve() not in path.parents or not path.is_file():
            return self._send(404, {"error": "not found"})
        self._send(200, path.read_bytes(),
                   CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    # -- routes --------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path
        try:
            if path in ("/", "/index.html"):
                return self._file(STATIC, "index.html")
            if path.startswith("/static/"):
                return self._file(STATIC, path[len("/static/"):])
            if path == "/api/decks":
                from web_client.match import deck_choices
                from web_client import catalog, profile
                decks = deck_choices()
                names = catalog.describe({"cards": sorted(
                    {d["champion_guid"] for d in decks})})["cards"]
                for deck in decks:
                    champ = names.get(deck["champion_guid"].lower()) or {}
                    deck["champion"] = champ.get("name", "")
                # ``saved``: the player's own decks that can be played (see profile.play_decks).
                return self._send(200, {"decks": decks, "saved": profile.play_decks()})
            if path in ("/api/profile", "/api/collection", "/api/profile/decks", "/api/profile/champions",
                        "/api/profile/gems"):
                from web_client import profile
                format_name = (query.get("format") or [None])[0]
                body = {"/api/profile": profile.profile_summary,
                        "/api/collection": lambda: {"cards": profile.collection()},
                        "/api/profile/decks": lambda: {"decks": profile.decks(format_name)},
                        "/api/profile/champions": lambda: {"champions": profile.champions(format_name)},
                        "/api/profile/gems": lambda: {"gems": profile.gem_choices()}}[path]()
                return self._send(200, body)
            deck = re.fullmatch(r"/api/profile/decks/(\d+)", path)
            if deck:
                from web_client import profile
                found = profile.deck(int(deck.group(1)))
                if found is None:
                    return self._send(404, {"error": "no such deck"})
                return self._send(200, found)
            art = re.fullmatch(r"/art/(a\d{7})\.jpg", path)
            if art:
                from web_client import catalog
                found = catalog.art_path(art.group(1))
                if found is None:
                    return self._send(404, {"error": "no art"})
                return self._send(200, found.read_bytes(), "image/jpeg")
            captured = re.fullmatch(r"/api/matches/(m\d+)/capture", path)
            if captured:
                match = MATCHES.get(captured.group(1))
                if match is None:
                    return self._send(404, {"error": "no such match"})
                return self._send(200, match.export_capture())
            matched = re.fullmatch(r"/api/matches/(m\d+)", path)
            if matched:
                match = MATCHES.get(matched.group(1))
                if match is None:
                    return self._send(404, {"error": "no such match"})
                after = int((query.get("after") or ["0"])[0])
                wait = min(25.0, float((query.get("wait") or ["0"])[0]))
                return self._send(200, match.snapshot(after, wait))
            self._send(404, {"error": "not found"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/matches":
                return self._send(200, {"id": MATCHES.create(body)})
            if path == "/api/profile/decks/validate":
                from web_client import profile
                return self._send(200, profile.validate_deck(body))
            if path == "/api/profile/decks":
                from web_client import profile
                return self._send(200, profile.save_deck(body))
            if path == "/api/catalog":
                from web_client import catalog
                return self._send(200, catalog.describe(body))
            matched = re.fullmatch(r"/api/matches/(m\d+)/action", path)
            if matched:
                from web_client.match import ActionError
                match = MATCHES.get(matched.group(1))
                if match is None:
                    return self._send(404, {"error": "no such match"})
                try:
                    match.submit(body)
                except ActionError as exc:
                    return self._send(409, {"error": str(exc)})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except (KeyError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(500, {"error": str(exc)})

    def do_DELETE(self):
        deck = re.fullmatch(r"/api/profile/decks/(\d+)", urlparse(self.path).path)
        if deck:
            from web_client import profile
            return self._send(200, {"deleted": profile.delete_deck(int(deck.group(1)))})
        matched = re.fullmatch(r"/api/matches/(m\d+)",
                               urlparse(self.path).path)
        if not matched:
            return self._send(404, {"error": "not found"})
        self._send(200, {"dropped": MATCHES.drop(matched.group(1))})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=os.environ.get(
        "HEX_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get(
        "HEX_WEB_PORT", "8780")))
    parser.add_argument("--live-db", action="store_true",
                        help="play on HEX_DB_PATH/hconnect.db, not a snapshot")
    args = parser.parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if not args.live_db:
        print(f"[web-client] database snapshot: {use_snapshot_database()}",
              flush=True)
    # Importing the engine opens the database named by HEX_DB_PATH.
    import web_client.match  # noqa: F401
    # Importing the engine opens the database named by HEX_DB_PATH.
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"[web-client] http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
