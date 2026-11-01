"""JSON projection of session events and the packet capture hook.

The Unity client receives ``NetworkPacketSessionEventArgs`` as binary. The
browser receives the same events as plain JSON objects: the event class ID,
the client event name, and the event's public fields with UIDs reduced to
integers and ResourceIds to GUID strings. Fields starting with ``_`` are
server-side projection metadata and never leave the process.
"""

from __future__ import annotations

import threading
import uuid

from domain.types import CombatId, ResourceId, SessionCardId, UID

_SKIP_FIELDS = {"ser", "session_id"}
_capturing = threading.local()


def _value(value):
    if isinstance(value, SessionCardId):
        return int(value.uid.uid64)
    if isinstance(value, UID):
        return int(value.uid64)
    if isinstance(value, ResourceId):
        return str(value.guid)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, CombatId):
        return {"attacker": _value(value.attacker), "serial": value.serial}
    if hasattr(value, "CLASS_ID") and hasattr(value, "ser"):
        return event_to_json(value)
    if isinstance(value, dict):
        return {str(_value(k)): _value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_value(v) for v in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def event_to_json(event) -> dict:
    """Return one SessionEventArgs as a JSON-safe dict."""
    out = {"cls": int(event.CLASS_ID),
           "type": type(event).__name__.replace("SessionEventArgs", "")}
    for name, value in vars(event).items():
        if name.startswith("_") or name in _SKIP_FIELDS:
            continue
        out[name] = _value(value)
    return out


class PacketCapture:
    """Record every packet the server builds for a set of player IDs.

    ``Game.make_network_packet(player_uid)`` is the single per-recipient
    boundary every 3055 send passes through (PvE, PvP, setup, options), and
    its result has already had hidden-zone and reveal filtering applied for
    that recipient. Wrapping it once per process lets the web gateway see the
    exact event list a Unity client would have decoded, without decoding
    bytes. Packets for players that no web seat watches are ignored.
    """

    _installed = False
    _install_lock = threading.Lock()
    _watchers: dict[int, "PacketCapture"] = {}

    def __init__(self, player_id: int):
        self.player_id = int(player_id)
        self.packets: list[list[dict]] = []
        self._lock = threading.Lock()

    @classmethod
    def install(cls):
        with cls._install_lock:
            if cls._installed:
                return
            from domain.events import NetworkPacketSessionEventArgs
            from domain.game import Game
            original_make = Game.make_network_packet
            original_add = NetworkPacketSessionEventArgs.add_event

            def add_event(packet, arg):
                original_add(packet, arg)
                # The packet keeps only serialized bytes; keep the JSON view
                # alongside while the event object is still at hand.
                web_events = packet.__dict__.get("_web_events")
                if web_events is not None:
                    web_events.append(event_to_json(arg))

            def make_network_packet(game, player_uid):
                watcher = None
                try:
                    raw = int(getattr(player_uid, "uid64", player_uid))
                    watcher = cls._watchers.get(raw >> 8)
                except (TypeError, ValueError):
                    pass
                if watcher is None:
                    return original_make(game, player_uid)
                token = _capturing.__dict__.get("events")
                _capturing.events = []
                try:
                    packet = original_make(game, player_uid)
                finally:
                    events = _capturing.events
                    _capturing.events = token
                watcher._record(events)
                return packet

            def packet_init(packet):
                original_init(packet)
                events = _capturing.__dict__.get("events")
                if events is not None:
                    packet._web_events = events

            original_init = NetworkPacketSessionEventArgs.__init__
            NetworkPacketSessionEventArgs.__init__ = packet_init
            NetworkPacketSessionEventArgs.add_event = add_event
            Game.make_network_packet = make_network_packet
            cls._installed = True

    def start(self):
        self.install()
        PacketCapture._watchers[self.player_id] = self
        return self

    def stop(self):
        if PacketCapture._watchers.get(self.player_id) is self:
            PacketCapture._watchers.pop(self.player_id, None)

    def _record(self, events):
        with self._lock:
            self.packets.append(events)

    def drain(self) -> list[list[dict]]:
        with self._lock:
            packets, self.packets = self.packets, []
        return packets
