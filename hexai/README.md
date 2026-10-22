# HexAI — self-play training for the Hex AI

`hexai/` is an **offline** training tool for the headless AI. It does not
replace the authored heuristics; it learns a small policy/value model from
AI-vs-AI games and, when explicitly enabled, lets that model rerank the AI's
main-phase card choices. Everything is opt-in at runtime, so an untouched
server behaves exactly as before.

```
pvp_autoplay.py --fra        capture JSONL        hexai.train           model.json
   (AI vs AI)        ──────►  (HEXAI_CAPTURE_DIR) ──────►  (numpy or CUDA) ──────►
                                                                                   │
                                              live server / headless game ◄────────┘
                                              (HEXAI_MODEL, opt-in agent)
```

## Why not MCTS on the live engine?

MCTS needs thousands of cheap `clone → step → score` cycles per decision. The
Hex battle state is a dictionary plus `game_cards` rows in SQLite, and one
12-turn FRA duel takes roughly 50 seconds headless. There is no cheap state
clone, so a real-engine MCTS is far slower than a game. The chosen approach is
the one that scales on this engine: generate many self-play games once, train a
compact model offline, then run that model in milliseconds per decision.

The same capture format is usable later if you want to add MCTS *inside a
learned model* (a learned simulator) or a MageZero-style transformer; only
`hexai.model` and `hexai.train` need to change.

## Model

A factorised sparse policy/value network, shared embedding table:

```
s            = mean(E[f] for f in state feature ids)
a            = mean(E[k] for k in action feature ids)
policy logit = s · a / sqrt(dim) + bias[action key]
value        = tanh(v_w · s + v_b)
```

* Feature ids are 63-bit `blake2b` hashes of canonical ASCII keys
  (`hexai/features.py`). Unknown ids are dropped at inference, so a model can
  be trained on one deck and loaded anywhere without a fixed vocabulary.
* State features are game-shaped (turn, life, thresholds, resources, board
  pressure, hand shape) plus per-card template/keyword rows, and only public
  information for the opponent.
* Action features use card templates and target *templates*, never raw session
  card UIDs, so rows are reusable across games.
* `hexai/model.py` is pure Python (no numpy) because the live server imports it
  only when `HEXAI_MODEL` is set.

## Capturing self-play data

`pvp_autoplay.py` is the existing headless AI-vs-AI runner. Set
`HEXAI_CAPTURE_DIR` to write one JSONL shard per process:

```bash
HEXAI_CAPTURE_DIR=/tmp/hexai-capture \
  python3 pvp_autoplay.py --fra --turn-limit 12 --workers 4 --runs 50
```

Each decision is recorded with the feature ids, every legal main-phase
candidate, the heuristic choice, and the final game result. Consecutive
duplicate decisions are collapsed, and the capture path is a no-op when the
variable is unset. The runner copies the live DB to a temporary SQLite snapshot
when `--fra` is used, so it never mutates `hconnect.db`.

To train one encounter deck (rather than the default FRA pair), pass its two
deck GUIDs to `--fra`:

```bash
HEXAI_CAPTURE_DIR=/tmp/hexai-capture \
  python3 pvp_autoplay.py --fra <deck_guid_a> <deck_guid_b> \
  --turn-limit 12 --workers 4 --runs 100
```

The same deck guid can be used on both sides for mirror self-play. Keep the
capture directory per deck (e.g. `/tmp/hexai-capture/<deck_guid>`) so shards
from different matchups do not mix.

Captured decision kinds:

| kind | enumeration | training use |
|---|---|---|
| `main` | full candidate list (cards + pass/keep) | policy + value |
| `attack` | chosen set, `attack:none`, `attack:all` | value (policy only has 2–3 options) |
| `block` | chosen blocks, `block:none` | value |
| resource/choice | not captured yet | — |

## Training

The default backend needs only numpy:

```bash
python3 -m hexai.train --data /tmp/hexai-capture \
  --out models/angel.json --dim 64 --epochs 8
```

Policy targets: `greedy` (default, softmax over the heuristic candidate
scores — imitation), `chosen` (one-hot on the action the heuristic took), or
`outcome` (one-hot weighted by the game result). Value targets are the seat's
final result (`+1` win, `-1` loss, `0` draw/unfinished). A game that stops at
the turn limit contributes policy samples but no value sample.

### CUDA (optional)

Training on a GPU uses PyTorch and is entirely optional. Install it in a
dedicated venv so the server environment is untouched:

```bash
python3 -m venv .venv-hexai
.venv-hexai/bin/python -m pip install numpy torch \
  --index-url https://download.pytorch.org/whl/cu128
```

Then:

```bash
.venv-hexai/bin/python -m hexai.train --data /tmp/hexai-capture \
  --out models/angel.json --dim 64 --epochs 8 \
  --backend torch --device auto
```

`--device` accepts `auto`, `cuda`, `cuda:0`, or `cpu`. The torch backend pads
each batch into `(B, S)` state ids and `(B, C, A)` action ids with masks, so a
whole batch is a handful of kernels instead of a Python loop. It trains the
same model and exports the same `model.json`.

What the GPU actually buys you:

* The *current* model is tiny; on a 3070/3080 the bottleneck is simulation
  (≈50 s per game), not the trainer. Use the GPU when the replay buffer is
  large or when you widen the model later.
* The batched torch backend is already faster than numpy on CPU once a buffer
  has tens of thousands of decisions (batching beats the numpy trainer's
  per-sample loop). CUDA only overtakes the CPU backend when the embedding
  vocabulary, model width, or batch size grow enough to amortise kernel
  launches; for the small model it is roughly a wash.
* Bigger batches and a wider/transformer model are where CUDA matters. The
  capture format already carries the sparse ids needed for that upgrade.
* A CUDA-capable PyTorch install is ~2.5 GB; the numpy path stays the default
  for a reason.

## Running the trained agent

```bash
HEXAI_MODEL=models/angel.json \
  python3 pvp_autoplay.py --fra --turn-limit 12 --runs 20
```

Environment knobs (`hexai/agent.py`):

| variable | default | meaning |
|---|---|---|
| `HEXAI_MODEL` | unset | model path; unset disables the agent |
| `HEXAI_HEURISTIC_MIX` | `0.35` | weight of normalised heuristic score added to the network logit; `0` = pure network |
| `HEXAI_TEMPERATURE` | `1.0` | softmax temperature for sampling |
| `HEXAI_EXPLORE` | `0` | probability of a uniformly random candidate |
| `HEXAI_SAMPLE` | `0` | `1` samples from the distribution instead of argmax |
| `HEXAI_DEBUG` | `0` | `1` logs candidate lists/overrides via `log_req` |

The agent only reranks the **main phase**. Turn phases, champion powers,
warzone abilities, combat declaration, blocking, mulligans, and resource plays
stay on the authored heuristic. That keeps the risk small while a model is
being trained, and it is why the runtime fallback is always the heuristic
decision when the model has no useful preference.

A model loaded by the server is cached per path+mtime; replacing the file
hot-swaps on the next decision without a restart. `HEXAI_MODEL` only matters
for processes that call the headless AI; the live server is unaffected unless
you launch it with the variable set.

## Practical notes

* Train one model per encounter/deck if you want deck-local priors; the state
  features already include the deck's cards, champion, and thresholds.
* Start with `--target greedy` for stable imitation, then compare `chosen` and
  `outcome` on a holdout set. `*.metrics.json` next to the model records the
  split and metrics.
* Unfinished games are common at low `--turn-limit`; raise the limit for value
  training.
* The capture hook is inside `pvp_autoplay.py`; it is deliberately not imported
  by the battle server, so a missing/broken model cannot affect live matches.
* Keep `hexai/` free of new runtime dependencies. `numpy` is training-only;
  `torch` is training-only and optional.

## Tests

```bash
python3 tests/tests_hexai.py
```

Covers deterministic feature ids, resource exclusion from candidates, capture
round-trip/dedupe, the agent override/fallback path, and end-to-end numpy
training on a toy shard. Run the same file with `.venv-hexai/bin/python` to
include the torch imports (the file itself stays dependency-free).
