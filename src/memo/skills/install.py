"""Skills install: fetch SKILL.md raw, validate frontmatter, store like put."""
from __future__ import annotations

import sqlite3

from memo import errors
from memo.skills.crud import build_frontmatter, get, put


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return None, text
    meta: dict = {}
    for line in lines[1:end]:
        if ":" in line:
            field_name, field_value = line.split(":", 1)
            meta[field_name.strip().lower()] = field_value.strip().strip("'\"")
    return meta, "\n".join(lines[end + 1:])


def default_fetch(url: str) -> str:
    import httpx  # lazy: importing memo must never load the network stack

    try:
        response = httpx.get(url, timeout=15.0, follow_redirects=True)
    except Exception as error:  # network down/timeout -> internal, no row (F-04)
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        raise RuntimeError(f"fetch failed: {first}")
    if response.status_code != 200:
        raise RuntimeError(f"fetch failed: HTTP {response.status_code} for {url}")
    return response.text


def install(conn: sqlite3.Connection, repository: str, skill_name: str,
            fetch=None) -> dict:
    """Fetch SKILL.md raw, validate frontmatter, then behave EXACTLY like put
    (plus a files_to_write delta so file harnesses materialize immediately).
    fetch(url)->str is injectable; tests MUST mock it (M-020, no live net)."""
    fetch = fetch or default_fetch
    if repository.startswith("skills.sh/"):
        slug = repository[len("skills.sh/"):]
        url = f"https://skills.sh/{slug}/skills/{skill_name}/SKILL.md"
        source = "skills.sh"
    elif repository.startswith("github:"):
        remainder = repository[len("github:"):]
        reference = "main"
        if "@" in remainder:
            remainder, reference = remainder.rsplit("@", 1) or (remainder, reference)
            if not reference:
                reference = "main"
        url = (f"https://raw.githubusercontent.com/{remainder}/{reference}/skills/"
               f"{skill_name}/SKILL.md")
        source = f"github:{remainder}"
    else:
        return errors.err("not_found", f"unknown repo {repository!r}")
    try:
        text = fetch(url)
    except Exception as error:  # noqa: BLE001 — boundary: map to envelope
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        detail = first if first.startswith("fetch failed") else f"fetch failed: {first}"
        return errors.err("internal", detail)
    meta, body = split_frontmatter(text)
    if not meta or not meta.get("name") or not meta.get("description"):
        return errors.err("not_found", f"frontmatter invalid for {skill_name!r}")
    if meta["name"] != skill_name:
        return errors.err(
            "not_found",
            f"frontmatter name mismatch: {meta['name']!r} != {skill_name!r}")
    receipt = put(conn, skill_name, meta["description"], body, source=source)
    row = get(conn, skill_name)
    receipt["files_to_write"] = [{
        "path": f"skills/{skill_name}/SKILL.md",
        "content": build_frontmatter(
            skill_name, row["description"], row["version"], row["body"]),
        "hash": receipt["hash"],
    }]
    return receipt
