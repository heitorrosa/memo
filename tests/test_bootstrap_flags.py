from memo import db, prompts as prompt_store, summary
from memo.server.bootstrap import bootstrap_text as server_bootstrap_text


def mem(tmp_path):
    conn = db.connect(str(tmp_path / "memo.db"))
    assert db.migrate(conn) == 3
    return conn


def test_bootstrap_always_on(tmp_path):
    conn = mem(tmp_path)
    text = server_bootstrap_text(conn)
    assert "DISABLED" not in text
    assert prompt_store.render("orientation") in text
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
