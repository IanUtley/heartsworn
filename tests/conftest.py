"""Supply the ``db`` fixture the direct-run test files expect under pytest.

Each test module builds its own disposable database in ``make_db()`` and
binds it to ``db._db`` (the same steps its ``run()`` helper performs when the
file is executed as a script).  This fixture repeats those steps for pytest.
"""

import pytest

# Opt-in catalogue audit: run it directly, not as part of the full suite.
collect_ignore = ["tests_card_play_matrix.py","tests_set1_pvp_sweep.py","tests_core_sets_sweep.py"]


@pytest.fixture
def db(request):
    import db as dbmod

    make = getattr(request.module, "make_db", None)
    if make is None:
        pytest.skip("module has no make_db(); run it as a script instead")
    import importlib
    # Tests rebind ``_db`` on several modules and some never restore it; a
    # stale binding would leak one test's database into the next.
    bound = []
    for name in ("db", "hconnect_server", "ai", "pvp_db", "ai_eval"):
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(module, "_db"):
            bound.append((module, module._db))
    # The card-projection memo is keyed by session and card uid, which tests
    # reuse across their separate databases; start each test with it empty.
    try:
        from rules_port import targeting
        targeting._CARD_MEMO.clear()
    except Exception:
        pass
    connection = make()
    # Count writes made through this plain connection the way the server's
    # wrapped connection does, so write-versioned projection memos stay valid.
    from db import _note_write, _WRITE_KEYWORDS

    def _trace(statement):
        if statement.lstrip().split(None, 1)[:1] and \
                statement.lstrip().split(None, 1)[0].upper() in _WRITE_KEYWORDS:
            _note_write()

    connection.set_trace_callback(_trace)
    dbmod._db = connection
    try:
        yield connection
    finally:
        for module, previous in bound:
            module._db = previous
        # Modules first imported during the test (``from db import _db``)
        # captured this connection; rebind them before it is closed so a later
        # test does not inherit a closed database.
        import sys
        for module in list(sys.modules.values()):
            if getattr(module, "_db", None) is connection:
                module._db = dbmod._db
        connection.close()
