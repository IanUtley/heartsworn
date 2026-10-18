"""Browser battle client for the Heartsworn server.

``server.py`` serves the static battle UI and a small JSON API. Each browser
seat is a virtual HConnect player inside the normal tournament PvP session
path: its transactions go through ``HCPHandler.handle_service_request`` and
the session events the server publishes for it are captured at the
``Game.make_network_packet`` boundary, after per-recipient visibility
filtering, and replayed into a browser-side state cache.
"""
