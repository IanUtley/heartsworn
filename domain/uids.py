"""Protocol UID type codes, the UID packing rule, and the service UIDs built from them."""

from domain.constants import (
    SERVICE_AI_UID_TYPE,
    SERVICE_GAME_SESSION_UID_TYPE,
    SERVICE_PLAYER_UID_TYPE,
)

UID_TYPE = {
    "ServicePlayer": SERVICE_PLAYER_UID_TYPE,
    "ServiceAI": SERVICE_AI_UID_TYPE,
    "ServiceMail": 252,
    "ServiceProfile": 245,
    "ServiceGameSession": SERVICE_GAME_SESSION_UID_TYPE,
    "ServiceMatchmaking": 247,
    "ServiceEscrow": 249,
    "ServiceCampaign": 253,
}


def make_uid(type_byte: int, instance_id: int) -> int:
    """Pack a UID: type in the low byte, instance in the high 56 bits."""
    return (type_byte & 0xFF) | ((instance_id & 0x00FFFFFFFFFFFFFF) << 8)


SERVICE_MAIL_UID = make_uid(UID_TYPE["ServiceMail"], 0)
SERVICE_PROFILE_UID = make_uid(UID_TYPE["ServiceProfile"], 0)
SERVICE_GAME_SESSION_UID = make_uid(UID_TYPE["ServiceGameSession"], 0)
SERVICE_MATCHMAKING_UID = make_uid(UID_TYPE["ServiceMatchmaking"], 0)
SERVICE_PLAYER_UID = make_uid(UID_TYPE["ServicePlayer"], 67890)
