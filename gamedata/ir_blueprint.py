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
    """``NAME <- SOURCE "text"``: a target found by name, with its rule text."""
    name: str
    source: str
    line: int
    text: str | None = None


@dataclass
class Step:
    """``NAME : ACTION(args) [-> OUT] ["text"]``."""
    name: str
    action: str
    args: list[Any]
    out: str | None
    line: int
    text: str | None = None


@dataclass
class Card:
    name: str
    card_type: str
    cost: int | None
    steps: list[Binding | Step] = field(default_factory=list)
    text: str | None = None   # the card's rule text, after the header


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
        self.tokens = list(tokens)
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
        card = Card(name.text, card_type, cost, text=self.optional_text())
        self.take("newline")
        self.skip_newlines()
        while self.peek().kind == "indent":
            card.steps.append(self.line_item())
            self.skip_newlines()
        self.take("eof")
        return card

    def line_item(self) -> Binding | Step:
        self.take("indent")
        name = self.take("name")
        if self.peek().text == "<-":
            self.take("arrow_in")
            source = self.take("name")
            text = self.optional_text()
            self.take("newline") if self.peek().kind == "newline" else None
            return Binding(name.text, source.text, name.line, text)
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
        text = self.optional_text()
        if self.peek().kind == "newline":
            self.take("newline")
        return Step(name.text, action.text, args, out, name.line, text)

    def optional_text(self) -> str | None:
        if self.peek().kind == "string":
            return self.take("string").text[1:-1]
        return None

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


# A rule text the Blueprint does not carry yet. Steps have no text of their own, so generated effects are marked.
MISSING_TEXT = "[rule text not in the Blueprint yet]"

# The stored effects a Blueprint binding or step refers to, by name (resolved through ir_surface.shared).
BINDING_SOURCES: dict[str, str] = {"OpposingChampion": "AATargetTargetOpposingChampion"}
REVEAL_EFFECT = "lookAt"


@dataclass
class Lowered:
    """The card's objects: the root's clauses in order (effect reference, target index), the local Choose ability, and
    the invoke effect that runs it."""
    clauses: list[dict[str, Any]]
    local: Any
    invoke: Any


def lower(card: Card) -> Lowered:
    """Lower a checked card to surface objects. Targets are numbered in step order; a Choose step becomes a local
    ability, and the Discard step that consumes its output is placed inside that ability."""
    from gamedata import ir_surface as S
    from gamedata.ir_python import _clause

    consumers = {}
    for item in card.steps:
        if isinstance(item, Step):
            for arg in item.args:
                for ref in _references(arg):
                    consumers.setdefault(ref, []).append(item)

    targets: dict[str, int] = {}
    clauses: list[dict[str, Any]] = []
    local = invoke = None
    next_index = 0   # the next target index a clause may name
    for item in card.steps:
        if isinstance(item, Binding):
            if item.source not in BINDING_SOURCES:
                raise BlueprintError(item.line, f"no stored target for {item.source!r}")
            effect = S.shared(BINDING_SOURCES[item.source])   # its stored text is the Records'; the Blueprint text is kept
            targets[item.name] = next_index
            clauses.append(_clause(effect_template_id=effect.ref, target_template_index=next_index,
                                   effect_instance_id=len(clauses), effect_group_id=len(clauses) + 1))
            next_index += 1
        elif item.action == "Reveal":
            item.args[0].split(".", 1)[0]  # validates the argument shape, as before
            effect = S.shared(REVEAL_EFFECT)
            # The hand is its own target (the Records give it a separate target template), after the champion's.
            targets[item.out] = next_index
            clauses.append(_clause(effect_template_id=effect.ref, target_template_index=next_index,
                                   effect_instance_id=len(clauses), effect_group_id=len(clauses) + 1,
                                   secondary_target_index=0))
            next_index += 1
        elif item.action == "Choose":
            after = [s for s in consumers.get(item.out, []) if isinstance(s, Step) and s.action == "Discard"]
            if len(after) != 1:
                raise BlueprintError(item.line, f"the output {item.out!r} must go to exactly one Discard")
            discard = after[0]
            # Choose stores the picked card, then Discard discards it: the Records' two effects, in that order.
            store = S.store_target(item.text or MISSING_TEXT, id=S._stable_guid("effect", "store " + item.name))
            drop = S.discard(discard.text or MISSING_TEXT, id=S._stable_guid("effect", discard.name),
                             owner=str(discard.args[1]["value"]))
            local = S.choose_revealed(
                item.text or MISSING_TEXT,
                id=S._stable_guid("ability", card.name + item.name), target_id=S._stable_guid("target", card.name + item.name),
                card_filter=S.non_resource(), does=[store, drop])
            invoke_id = S._stable_guid("effect", "invoke " + item.name)
            invoke = S.activate(MISSING_TEXT, id=invoke_id, ability=local)
            clauses.append(_clause(effect_template_id=S._ref(invoke_id),
                                   target_template_index=next_index,
                                   effect_instance_id=len(clauses), effect_group_id=len(clauses) + 1))
            next_index += 1
        elif item.action == "Discard":
            continue   # placed inside the local ability above
        else:
            raise BlueprintError(item.line, f"no lowering for {item.action}")
    if local is None:
        raise BlueprintError(card.steps[-1].line, "the card has no Choose step")
    return Lowered(clauses=clauses, local=local, invoke=invoke)


# The Records' targets of Inquisition's root ability (champion, revealed hand, player). Until targets are named in the
# surface, the root names these stored records by GUID, as the Records do.
ROOT_TARGETS = ["0a64e181-c49b-35c6-5c47-1b95924a7c48", "584ae879-0468-bb00-ff27-7a5ff46c5bc2",
                "614afb2a-a723-797c-5a34-faae1c64cdd7"]


def emit(card: Card, game_text: str | None = None) -> str:
    """The card as ir_python text: the local Choose ability with its target and discard, the invoke effect, then the
    root ability. The rule text is the card's own, or ``game_text`` when the card has none."""
    from gamedata import ir_python
    from gamedata import ir_surface as S

    lowered = lower(card)
    local = lowered.local
    game_text = card.text or game_text or ""
    lines = [f"# {card.name}: {game_text}", "# Written from the Blueprint card by gamedata/ir_blueprint.emit."]
    lines.extend((f"CHOOSE_TARGET_{index} = {ir_python.dumps(target)}") for index, target in (enumerate(local._surface_targets)))
    lines.extend((f"CHOOSE_DOES_{index} = {ir_python.dumps(effect)}") for index, effect in (enumerate(local._surface_does)))
    lines.append(f"CHOOSE = {ir_python.dumps(local)}")
    lines.append(f"INVOKE = {ir_python.dumps(lowered.invoke)}")
    root = S.ability(game_text, id=S._stable_guid("ability", game_text), ability_effect_list=lowered.clauses,
                     ability_target_template_ids=[S._ref(guid) for guid in ROOT_TARGETS])
    lines.append(f"ROOT = {ir_python.dumps(root)}")
    return "\n".join(lines) + "\n"
