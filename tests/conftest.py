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


@pytest.fixture(autouse=True)
def _restore_shared_database():
    """Put back the shared database binding after every test.

    Some helpers (``ir_equivalence._scenario``) point ``db._db`` at a fresh
    in-memory copy and never restore it, so every later test in the run would
    otherwise read that copy instead of the live test database.
    """
    import sys
    import db as dbmod

    previous = dbmod._db
    holders = [(module, module._db) for module in list(sys.modules.values())
               if getattr(module, "_db", None) is previous]
    yield
    for module, held in holders:
        module._db = held
    dbmod._db = previous
