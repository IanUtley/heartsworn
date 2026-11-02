"""Blueprint-style card text: a card as named steps whose outputs feed later steps.

    Inquisition [BasicAction, cost 2]
      Target  <- OpposingChampion
      Look    : Reveal(Target.Hand)                  -> Revealed
      Choose  : Choose(Revealed, NonResource)        -> Pick
      Discard : Discard(Pick, Owner=Target)

A header names the card, its type and cost. ``NAME <- SOURCE`` binds a target. ``NAME : ACTION(args) [-> OUT]``
is a step; ``OUT`` names the step's result for later steps. Arguments are names, dotted names (``Target.Hand``),
integers, strings, or ``key=value``.

``parse`` builds the tree and checks it: every name is defined before use, no name is defined twice, and every step's
output is used by a later step (a step with no use is refused, since nothing would run it). Lowering to the IR text
(gamedata/ir_python.py) is a later step; this module only reads and checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TOKEN = re.compile(r"""
    (?P<ws>[ \t]+)
  | (?P<comment>\#[^\n]*)
  | (?P<arrow_in><-)
  | (?P<arrow_out>->)
  | (?P<string>"[^"\n]*")
  | (?P<int>\d+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[\[\]\(\),:.=])
  | (?P<newline>\n)
""", re.VERBOSE)


class BlueprintError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


@dataclass
class Token:
    kind: str
    text: str
    line: int
    indent: int = 0


@dataclass
class Binding:
    """``NAME <- SOURCE``: a target found by name."""
    name: str
    source: str
    line: int


@dataclass
class Step:
    """``NAME : ACTION(args) [-> OUT]``."""
    name: str
    action: str
    args: list[Any]
    out: str | None
    line: int


@dataclass
class Card:
    name: str
    card_type: str
    cost: int | None
    steps: list[Binding | Step] = field(default_factory=list)


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    indent = 0
    at_line_start = True
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise BlueprintError(line, f"unexpected character {text[pos]!r}")
        pos = match.end()
        kind = match.lastgroup
        value = match.group()
        if kind == "ws":
            if at_line_start:
                indent = len(value)
            continue
        if kind == "comment":
            continue
        if kind == "newline":
            tokens.append(Token("newline", value, line))
            line += 1
            indent = 0
            at_line_start = True
            continue
        if at_line_start:
            if indent:   # only indented lines are steps; the header and bindings at column 0 carry no indent token
                tokens.append(Token("indent", str(indent), line, indent))
            at_line_start = False
        tokens.append(Token(kind, value, line))
    tokens.append(Token("eof", "", line))
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = [t for t in tokens]
        self.at = 0

    def peek(self) -> Token:
        return self.tokens[self.at]

    def take(self, kind: str, text: str | None = None) -> Token:
        token = self.peek()
        if token.kind != kind or (text is not None and token.text != text):
            wanted = text or kind
            raise BlueprintError(token.line, f"expected {wanted!r}, found {token.text or token.kind!r}")
        self.at += 1
        return token

    def skip_newlines(self) -> None:
        while self.peek().kind == "newline":
            self.at += 1

    def card(self) -> Card:
        self.skip_newlines()
        name = self.take("name")
        self.take("punct", "[")
        card_type = self.take("name").text
        cost = None
        if self.peek().text == ",":
            self.take("punct", ",")
            self.take("name", "cost")
            cost = int(self.take("int").text)
        self.take("punct", "]")
        card = Card(name.text, card_type, cost)
        self.take("newline")
        self.skip_newlines()
        while self.peek().kind == "indent":
            card.steps.append(self.line_item())
            self.skip_newlines()
        self.take("eof")
        return card

    def line_item(self) -> Binding | Step:
        indent = self.take("indent")
        name = self.take("name")
        if self.peek().text == "<-":
            self.take("arrow_in")
            source = self.take("name")
            self.take("newline") if self.peek().kind == "newline" else None
            return Binding(name.text, source.text, name.line)
        self.take("punct", ":")
        action = self.take("name")
        self.take("punct", "(")
        args = []
        if self.peek().text != ")":
            args.append(self.argument())
            while self.peek().text == ",":
                self.take("punct", ",")
                args.append(self.argument())
        self.take("punct", ")")
        out = None
        if self.peek().kind == "arrow_out":
            self.take("arrow_out")
            out = self.take("name").text
        if self.peek().kind == "newline":
            self.take("newline")
        return Step(name.text, action.text, args, out, name.line)

    def argument(self) -> Any:
        token = self.peek()
        if token.kind == "string":
            self.at += 1
            return token.text[1:-1]
        if token.kind == "int":
            self.at += 1
            return int(token.text)
        if token.kind == "name":
            self.at += 1
            if self.peek().text == "=":
                self.take("punct", "=")
                return {"key": token.text, "value": self.value()}
            if self.peek().text == ".":
                parts = [token.text]
                while self.peek().text == ".":
                    self.take("punct", ".")
                    parts.append(self.take("name").text)
                return ".".join(parts)
            return token.text
        raise BlueprintError(token.line, f"expected an argument, found {token.text or token.kind!r}")

    def value(self) -> Any:
        token = self.peek()
        if token.kind in ("string", "int", "name"):
            return self.argument()
        raise BlueprintError(token.line, f"expected a value, found {token.text!r}")


def _check(card: Card) -> None:
    """The names are consistent.

    A binding's name and a step's output are defined once. A name in an argument is either a binding, an earlier
    step's output, or a library name (a filter or a record, resolved later by name); using a step's output before its
    step is refused. Every output is used by a later step, except the last step's, since nothing would consume it.
    """
    outputs = {item.out: item.line for item in card.steps if isinstance(item, Step) and item.out is not None}
    bound: dict[str, int] = {}
    for item in card.steps:
        if isinstance(item, Binding):
            if item.name in bound or item.name in outputs:
                raise BlueprintError(item.line, f"{item.name!r} is already defined")
            bound[item.name] = item.line

    defined: dict[str, int] = dict(bound)
    used: set[str] = set()
    for item in card.steps:
        if isinstance(item, Binding):
            continue
        for arg in item.args:
            for ref in _references(arg):
                if ref in outputs and ref not in defined:
                    raise BlueprintError(item.line, f"{ref!r} is used before the step that defines it")
                used.add(ref)
        if item.out is not None:
            if item.out in defined:
                raise BlueprintError(item.line, f"{item.out!r} is already defined")
            defined[item.out] = item.line

    steps = [item for item in card.steps if isinstance(item, Step)]
    for step in steps[:-1]:
        if step.out is not None and step.out not in used:
            raise BlueprintError(step.line, f"the output {step.out!r} is never used")


def _references(arg: Any) -> list[str]:
    """The names an argument refers to: a bare name, the head of a dotted name, or a keyword's value."""
    if isinstance(arg, dict):
        return _references(arg["value"])
    if isinstance(arg, str) and arg and (arg[0].isalpha() or arg[0] == "_"):
        return [arg.split(".", 1)[0]]
    return []


def parse(text: str) -> Card:
    """The card described by ``text``. Raises ``BlueprintError`` with the line of the first problem."""
    card = _Parser(_tokenize(text)).card()
    _check(card)
    return card


# The action table: what each Blueprint action lowers to in the Records' structure, and what is still missing.
# Choose is a local ability (decided): the card defines it inside itself, as the Records do for Inquisition, where the
# invoked ability has a revealed-card target and its own discard effect.
ACTIONS: dict[str, dict[str, str]] = {
    "Reveal": {
        "lowers_to": "RevealCards effect (lookAt) on the clause's target (the target's hand)",
        "helper": "shared('lookAt') + a binding for the hand; not yet in ir_surface",
    },
    "Choose": {
        "lowers_to": "ActivateAbility clause invoking a local ability whose SourceRevealed target carries the filter",
        "helper": "local ability with a revealed-card target; not yet in ir_surface",
    },
    "Discard": {
        "lowers_to": "move-to-discard effect inside the invoked ability",
        "helper": "move_card(text, id=, zone=) exists; Owner= argument is missing",
    },
}
