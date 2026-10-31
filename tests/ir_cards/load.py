"""Load a card file: the IR text in tests/ir_cards/<name>.py, as the names it binds."""

from pathlib import Path

from gamedata import ir_python


def load_card(path: str | Path) -> dict:
    """The card's names. They are registered qualified by the file's name (``burning.DEAL_DAMAGE``), so two
    cards can each bind the same name."""
    path = Path(path)
    return ir_python.loads(path.read_text(encoding="utf-8"), scope=path.stem)
