"""Regression: flag-off bootstrap must not crash; zero-MCP paste fallback live.

DEFECT 1: server/bootstrap.py referenced summary.ORIENTATION (never defined)
-> AttributeError whenever memory_on or skills_on was False. Source of truth
is skills.ORIENTATION (what summary.py itself renders).
DEFECT 2: spec 26.3 zero-MCP paste fallback must render in the bootstrap.
"""
from memo import db, prompts as prompt_store, summary
from memo.server.bootstrap import bootstrap_text as server_bootstrap_text


def _mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def test_flag_off_bootstrap_never_raises(tmp_path):
    """Each flag off (and both off) renders DISABLED markers, no AttributeError."""
    conn = _mem(tmp_path)
    both_off = server_bootstrap_text(conn, memory_on=False, skills_on=False)
    assert "memories DISABLED" in both_off and "skills DISABLED" in both_off
    assert prompt_store.render("orientation") in both_off

    mem_off = server_bootstrap_text(conn, memory_on=False, skills_on=True)
    assert "memories DISABLED" in mem_off and "skills DISABLED" not in mem_off

    skills_off = server_bootstrap_text(conn, memory_on=True, skills_on=False)
    assert "skills DISABLED" in skills_off and "memories DISABLED" not in skills_off

    memory_search = server_bootstrap_text(conn)  # both on: still clean
    assert "DISABLED" not in memory_search
    conn.close()


def test_web_paste_fallback_is_live(tmp_path):
    """26.3 fallback appears in the memo://summary (bootstrap) output."""
    conn = _mem(tmp_path)
    fallback = prompt_store.render("web_paste_fallback")
    assert "skills below are pasted" in fallback
    assert "Quote the skill name when using one." in fallback

    text = summary.bootstrap_text(conn)
    assert fallback in text
    # server path (memo://summary + bootstrap prompt) carries it too
    assert fallback in server_bootstrap_text(conn)
    conn.close()
