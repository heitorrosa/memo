import os
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "opencode-memo" / "hooks.js"


def test_hooks_has_userprofile_fallback():
    text = HOOKS.read_text(encoding="utf-8")
    assert 'process.env.HOME ?? process.env.USERPROFILE ?? "~"' in text


def test_hooks_resolved_path_has_no_junk_segment(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\Administrator")
    base = os.environ.get("HOME") or os.environ.get("USERPROFILE") or "~"
    resolved = os.path.join(base, ".config/opencode/skills")
    assert "~" not in Path(resolved).parts
    assert Path(resolved).parts == ("C:\\", "Users", "Administrator",
                                    ".config", "opencode", "skills")
