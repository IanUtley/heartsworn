# Browser battle client

A web version of the Hex battle screen that plays against the server's AI
through the normal RulesPort session path.

```bash
bash restart.sh                # or docker bootstrap: the client needs hconnect.db
python3 -m web_client.server   # http://127.0.0.1:8780/
```

Options: `--port`, `--host` (or `HEX_WEB_PORT` / `HEX_WEB_HOST`), and
`--live-db` to play on `HEX_DB_PATH`/`hconnect.db` instead of a private
snapshot. Set `HEX_CLIENT_ROOT` to a Hex install (the folder holding
`AssetBundles/`) to show real card and champion art; without it cards are drawn
with shard-coloured frames.

## How it talks to the server

```text
browser ──HTTP/JSON──> web_client.server ──3029 PlayerTransaction──> HCPHandler
   ^                          │                                        │
   └──── event packets <── PacketCapture <── Game.make_network_packet ─┘
```

* Each match is a disposable tournament PvP session with two virtual
  `HCPHandler` seats, set up exactly like `pvp_autoplay` does: authored FRA
  decks seeded into `game_cards`, then the normal ready/setup handshake.
* Browser moves become the same 3029 transactions the Unity client sends
  (`PlayTroopTransaction`, `ActivateAbilityTransaction`,
  `CommitTroopsToAttackTransaction`, `SetAbilityActivationDataTransaction`,
  ...) and enter through `HCPHandler.handle_service_request`, so the RulesPort
  validates and resolves them.
* What the browser shows comes only from the session events the server
  publishes for its player. `PacketCapture` wraps `Game.make_network_packet`,
  the per-recipient boundary every 3055 sync passes through after hidden-zone
  filtering, and records each event as JSON (`events_json.py`). The browser
  keeps its own state cache from `CardUpdated`, `PlayerUpdated`,
  `TurnPhaseUpdated`, `PlayerOptionList`, `AbilityPushedOnChain`, combat events
  and so on, the way Unity's `ClientStateCache` does.
* The opponent seat is driven by the same AI policy the FRA simulator uses
  (`_ai_main_decision`, `_ai_attack_decision`, `_ai_block_decision`, combat
  tricks). Damage-order transactions are sent automatically for both seats,
  as the Unity client does on entering a damage step.
* By default the server plays on an online backup of `hconnect.db` in a
  temporary directory, so browser matches never add session rows to the live
  database.

## Battle screen

The board is a painted arena seen at a slight tilt: a stone ring around a
field, braziers in the corners, and a centre medallion that shows a star in the
main phases and an axe in combat (with fire across the board). Both warzones
lie on a gently tilted table. The opponent's hand fans out along the top edge
(sleeve backs) and yours along the bottom. Champion portraits with life,
resources, charges, thresholds and champion powers sit at the left corners,
Deck / Graveyard / Void piles at the right corners, and the phase dial on the
left edge. Hovering a card shows the full card; clicking a pile lists its cards.

When a card leaves a hand it flies to the centre of the board, flashes, then
lands where the server put it: the warzone, or the Chain, which runs as a row
of cards across the centre until it resolves. Resources fly straight to their
player's resource row, and a resolving chain item bursts at the centre. The
opponent's cards are revealed as they fly, from the identity the server sends
once they leave its hidden hand.

Playable cards glow (from the server's `PlayerOptionList`); targets the server
offers in an option are highlighted when you play the card. Attack and block
declarations, trigger target pickers, play/draw and mulligan are prompts from
the server's current decision.

Card sleeves and gameboards are chosen in the lobby or the in-game menu and
remembered in the browser. The gameboards are named after battleboards in the
inventory data (Basic, Forest, Frost Ring, Badlands, Sanctum of Suffering,
Coastline) and drawn in CSS, since the client's 3D board scenes are not
extracted. The opponent's sleeve is derived from the sleeve ID in its
`DeckCreated` event.

## Files

| File | Role |
|---|---|
| `server.py` | HTTP server, JSON API, database snapshot |
| `match.py` | `WebMatch`: session setup, decision routing, AI seat, transactions |
| `events_json.py` | event-to-JSON projection and the packet capture hook |
| `catalog.py` | card / champion / ability descriptions and optional art |
| `static/` | the browser app (no build step) |
