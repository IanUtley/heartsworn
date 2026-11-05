"""ServiceLoadBalancer session requests (find, start, join, ready, reconnect).

Moved out of hconnect_server.HCPHandler._handle_service_request_legacy.
"""

from binascii import hexlify
import io
import json
import random
import struct
from db import log_req
import game_engine
import db
from encoder import encode_datawrapper
from encoder import compress_gzip
from encoder import encode_objfmt_response
import campaign
from domain.uids import SERVICE_MAIL_UID
from encoder import encode_sync_event
from domain.uids import UID_TYPE
from domain.uids import SERVICE_GAME_SESSION_UID
from domain.uids import make_uid
from pvp_db import db_ability_effect_rows
from gamemodes.fra_arena import _fra_arena_setup_values
from services.deck_editing import _STARTER_DECKS
from pvp_db import db_deck_active_gems
from profile_db import db_insert_card_instance
from domain.constants import SERVICE_AI_UID_TYPE
from pvp_db import db_template_ability_guids
from pve_db import db_get_arena_state
from pvp_db import db_target_template_filter
from pvp_db import db_template_exists
from pvp_db import db_template_card_info
from pvp_db import db_insert_game_cards
from pvp_db import db_ability_metadata
from profile_db import db_get_user
from pve_db import db_get_player_champion_guid
from pve_db import db_champion_template_data_exists
from pve_db import db_extended_champion_health
from pve_db import db_champion_talents_for_deck
from pve_db import db_champion_template_name
from pve_db import db_get_fra_challengers
from pvp_db import db_get_charge_power
from pve_db import db_encounter_deck_sleeve
from profile_db import db_deck_sleeve
from pve_db import db_talent_ability_rows
from application.practice_flow import _push_pve_game_started
from profile_db import db_deck_champion_name
from pvp_db import db_clear_session_cards
from pvp_db import db_deck_cards_json
from pvp_db import db_first_template_guid
from pvp_db import db_update_game_card_abilities
from pvp_db import db_gem_templates
from pvp_db import db_encounter_deck_cards
from pvp_db import db_scene_mods
from pvp_db import db_ability_has_effect
import game_session
import encoder
from pve_db import db_campaign_owner_user_id


# Armies of Myth (AZ1 Node 66): the AI starts with the campaign's army level,
# the counter the conversation sets from the difficulty picked (0-6).
ARMY_OF_MYTH_SCENE = "0b1d5244-e9f6-46ec-bb06-73c36801f5ef"
ARMY_LEVEL_COUNTER = "fight_army_at_level"




# ServiceLoadBalancer — ReadyToStartGame (22031)






from services.ready_to_start import (  # moved; re-exported for existing callers
    _army_of_myth_ai_resources,
    handle_readytostartgame_22031,
)

LEGACY_HANDLERS = {
    22031: handle_readytostartgame_22031,
}
