"""HexAI: deck-local self-play tooling for the Hex AI.

The package has three deliberately separate layers:

``features`` / ``candidates``
    Pure state and action encoding.  It has no third-party dependencies and is
    safe to import from the live server: the encoder only reads the AI
    evaluator snapshot that ``ai_eval.build_evaluator`` already builds.

``capture``
    Opt-in instrumentation for headless games (``pvp_autoplay.py``).  It writes
    one JSONL shard per process; training converts those into arrays.

``train`` / ``train_torch`` / ``model`` / ``agent``
    Offline trainer (numpy by default, optional PyTorch/CUDA) and the runtime
    policy/value model (pure Python).  The runtime never imports numpy or torch
    so an untrained or disabled agent cannot change server dependencies.
"""

__all__ = ["features", "candidates", "capture", "model", "agent", "train",
           "train_torch"]
