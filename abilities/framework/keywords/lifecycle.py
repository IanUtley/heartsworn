"""Keyword lifecycle entry points.

Deploy, Inspire, and Deathcry share the CardEnteredZone/AsEntersPlay event
pipeline.  This facade gives callers a keyword-oriented home while the
existing trigger engine remains the compatibility implementation.
"""

from ..triggers import resolve_enters_play_triggers


