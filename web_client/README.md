# Browser battle client

A web version of the Hex battle screen that plays against the server's AI
through the normal RulesPort session path.

```bash
bash restart.sh                # or docker bootstrap: the client needs hconnect.db
python3 -m web_client.server   # http://127.0.0.1:8780/
```

Options: `--port`, `--host` (or `HEX_WEB_PORT` / `HEX_WEB_HOST`), and
`--live-db` to play on `HEX_DB_PATH`/`hconnect.db` instead of a private
snapshot.

The lobby asks for the `HEX SHARDS OF FATE` folder first. The page reads card
art, card-play sounds and battleboards from that folder in the browser
(`static/unity/`) and keeps them in memory; nothing is uploaded and the server
does not serve art. Battle stays disabled until the folder is read.

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

The arena, both warzones and the chain are a three.js scene
(`static/board3d.js`, with three.js 0.160 vendored under `static/vendor/`).
The hands, champions, piles, phase dial and prompts are HTML laid over it.
Cards are 3D objects that cast shadows, lift when hovered and glide between
places, and the braziers are real light sources. Open the page with `?flat` (or
use a browser without WebGL) to get the flat HTML board instead.

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

Gameboards are chosen in the lobby or the in-game menu and remembered in the
browser. The battleboard bundles (`AssetBundles/gameboards/*.ab`) are decoded in
the page: the playing surface is textured onto the arena field and the props
around it are three.js meshes. Deck daises sit on their deck anchors (`PlayerDeckAnchorTarget`,
`OpponentDeckAnchorTarget`). A board that places its own dais at the anchor (badlands'
`BattleBoard_*` copies) keeps that copy and leaves out its other copies of that side;
otherwise the authored dais is translated onto the anchor, since the client parents
it there and keeps its offset. Everything under a dais moves with it, its void portal
and fire ring included. The surface glows by its `_ESRA` mask: the red
channel lights the rune lines and the blue channel the reflective centre, in the
colours of the board's look preset in `board3d.js`. A board without an `_ESRA`
mask glows by its `_EmissionMap`, lit by the material's `_EmissionColor` (badlands'
lava lines). The bundles also hold the
imported models the board was built from (`MDL_*`, on Unity's
`Default-Material`); a copy is dropped when the board places the same mesh in
the arena itself. Props with no texture, and objects that the battleboard's
animation clips switch on and off (the void portal and intro effects), are left
out. The board is scaled to cover the screen, so no space beyond it shows at the
edges. Decoded boards and the board list are cached in the browser under a
version key (`board7:`, `boards7`); bump it when the decoder changes. Without
the folder the painted arena is used.
Card sleeves are still drawn in CSS from the sleeve ID in each `DeckCreated`
event.

Card-play sounds come from the card's `m_SoundEventOnSpawn` in the gamedata
(the catalog's `sound`). The clip is read from the card's own art bundle in
`AssetBundles/cardsets/*.harc`. About 97% of card clips resolve this way; the
rest are filed under another card's bundle or absent from the client.

## Client asset reader

`static/unity/` reads the Unity 5.6 containers without any server help:

| File | Role |
|---|---|
| `reader.js` | byte sources, LZ4 block data, UnityFS bundles, HARC archives |
| `lzma.js` | LZMA blocks (some card bundles) |
| `serialized.js` | serialized files, object table, generic TypeTree reader |
| `media.js` | Texture2D pixels (DXT1/DXT5, RGB/RGBA/ARGB, RGB565), Mesh attributes |
| `fsb.js` | FMOD FSB5 Vorbis banks remuxed to Ogg for `decodeAudioData` |
| `vorbis_setups.js` | the three Vorbis setup packets FSB banks refer to |
| `install.js` | the Hex folder: art index, card sounds, battleboard scenes |

The readers were checked against UnityPy output (object tables, texture pixels,
mesh attributes, and the decoded sound container). `vorbis_setups.js` is
extracted from vgmstream's FSB codebook table.

## Files

| File | Role |
|---|---|
| `server.py` | HTTP server, JSON API, database snapshot |
| `match.py` | `WebMatch`: session setup, decision routing, AI seat, transactions |
| `events_json.py` | event-to-JSON projection and the packet capture hook |
| `catalog.py` | card / champion / ability descriptions and optional art |
| `static/app.js` | state cache, HUD, prompts, card flights |
| `static/board3d.js` | three.js arena, cards and effects |
