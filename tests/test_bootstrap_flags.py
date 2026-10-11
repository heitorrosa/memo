from memo import db, prompts as prompt_store, summary
from memo.server.bootstrap import bootstrap_text as server_bootstrap_text


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def test_flag_off_bootstrap_never_raises(tmp_path):
    conn = mem(tmp_path)
    both_off = server_bootstrap_text(conn, memory_on=False, skills_on=False)
    assert "memories DISABLED" in both_off and "skills DISABLED" in both_off
    assert prompt_store.render("orientation") in both_off

    mem_off = server_bootstrap_text(conn, memory_on=False, skills_on=True)
    assert "memories DISABLED" in mem_off and "skills DISABLED" not in mem_off

    skills_off = server_bootstrap_text(conn, memory_on=True, skills_on=False)
    assert "skills DISABLED" in skills_off and "memories DISABLED" not in skills_off

    memory_search = server_bootstrap_text(conn)
    assert "DISABLED" not in memory_search
    conn.close()


def test_web_paste_fallback_is_live(tmp_path):
    conn = mem(tmp_path)
    fallback = prompt_store.render("web_paste_fallback")
    assert "skills below are pasted" in fallback
    assert "Quote the skill name when using one." in fallback

    text = summary.bootstrap_text(conn)
    assert fallback in text
    assert fallback in server_bootstrap_text(conn)
    conn.close()
