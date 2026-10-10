"""Regenerate the committed schema.sql artifact from memo.models."""
import pathlib

from memo.models.orm import render_schema

path = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(render_schema())
print(f"memo models -> {path}")
