"""ServiceTournaments requests (entry, reconnection, ladder records).

Moved out of hconnect_server.HCPHandler._handle_service_request_legacy.
"""

import gamemodes.tournament_server as tournament_server
from db import log_req
import db
from encoder import encode_datawrapper
from encoder import compress_gzip
from encoder import encode_objfmt_response
from domain.uids import SERVICE_MAIL_UID
from tournament_db import db_tournament_room_for_game
from gamemodes.tournament_engine import _is_corinth_room
from tournament_db import db_tournament_pool
from tournament_db import db_tournament_signup_by_player
from tournament_db import db_tournament_signup_set_async_state
from gamemodes.tournament_engine import CORINTH_SHARD_GUIDS
from gamemodes.tournament_engine import player_handler_lock
from tournament_db import db_tournament_match_session
from gamemodes.tournament_engine import player_handlers
from gamemodes.tournament_engine import push_tournament_room_data
from pvp_db import db_game_session_pids
from gamemodes.tournament_engine import player_decks
from tournament_db import db_tournament_signup_set_status
from tournament_db import db_seed_tournament_pool
from gamemodes.tournament_engine import _push_corinth_deck_construction
from tournament_db import db_tournament_signups_by_tournament
from domain.enums import ETournamentFormats
from pvp_db import db_delete_game_session
from tournament_db import db_tournament_player_run_live
from tournament_db import db_tournament_by_id
from gamemodes.tournament_engine import uid_instance
from gamemodes.tournament_engine import _encode_enter_tournament_error
from gamemodes.tournament_engine import _make_deck_data
from gamemodes.tournament_engine import start_waiting_room_game
from tournament_db import db_tournament_discard_match
from gamemodes.tournament_engine import try_start_corinth_match
from pvp_db import db_apply_tournament_main_deck
from gamemodes.tournament_engine import push_tournament_session_start
from tournament_db import db_tournament_pool_replace
from gamemodes.tournament_engine import record_tournament_forfeit
from tournament_db import db_tournament_status
from tournament_db import db_tournament_retire_player_run


# ServiceTournaments — TryReconnection (25021)
# ServiceTournaments — LadderRecord (70023)
# ServiceTournaments — TestTournamentEntry (25027)
# ServiceTournaments — EnterTournament (25029)
LEGACY_HANDLERS = {
}
