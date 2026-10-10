"""Peewee test-database pattern: every test gets its own tmp-file DB via
db.connect (which binds the deferred ORM database to that file), and this
autouse fixture closes Peewee handles afterwards so simultaneous-DB tests
(e.g. replay T-6 s2.db) never fight and file handles never leak.
"""
import pytest


@pytest.fixture(autouse=True)
def _peewee_close():
    yield
    try:
        from memo import db as memo_db

        for cached in list(getattr(memo_db, "_db_cache", {}).values()):
            try:
                if not cached.is_closed():
                    cached.close()
            except Exception:
                pass
        try:
            memo_db._db_cache.clear()
        except Exception:
            pass
        try:
            from memo.models.orm import database as peewee_database

            if not peewee_database.is_closed():
                peewee_database.close()
        except Exception:
            pass
    except Exception:
        pass
