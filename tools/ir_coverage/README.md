# tools/ir_coverage

Command-line reports for the IR interpreter and the native rules port. These
measure coverage and gaps. They are not part of the game server, and the
server does not import them.

| Script | What it reports |
| --- | --- |
| `ir_gap_report.py` | Records abilities the IR interpreter refuses, grouped by refusal reason. Regenerates the counts in `docs/IR_INTERPRETER_GAPS.md`. |
| `sweep_set1_coverage.py` | Set 1 cards whose effects or triggers have no executable handler. |
| `build_set1_coverage.py` | Set 1 ability and targeting inventory, with a representative card set. |
| `audit_effect_leaves.py` | Effect fields in the C# effect classes that the Python leaves never read. |
| `card_audit.py` | Static parity audit of card mechanics between the Python port and the C# client. |
| `coverage_log.py` | Reads the scoped lines of one isolated Unity headless coverage run. |

The IR card files in `tests/ir_cards/` are different: they are the input corpus that
`tests/ir_equivalence.py` runs (with `IR_EQ_CARDS=1`), not a report.

Run each script from the repository root, for example
`python3 tools/ir_coverage/ir_gap_report.py`.
