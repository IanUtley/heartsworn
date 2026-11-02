"""Pluggable pickers: how the server presents a choice to a player.

The engine builds a PickRequest (what is being chosen, from which candidates, within which bounds). A picker presents
it. UnityPicker sends the client's option-list events (the current behaviour). WebPicker serializes the same request
for web_client/. The engine never depends on which picker is in use.
"""

from pickers.request import BLOCK_DECLARATION_TEMPLATE, PickOption, PickRequest, PickTarget
from pickers.unity import present_options, present_pick
from pickers.web import to_web_message

__all__ = ["BLOCK_DECLARATION_TEMPLATE", "PickOption", "PickRequest", "PickTarget", "present_options", "present_pick", "to_web_message"]
