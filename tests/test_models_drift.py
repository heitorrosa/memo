"""Drift guard (Task 5): committed schema.sql is generated from memo.models."""
import pathlib

import memo.models
from memo.models import render_schema


def test_committed_schema_matches_generated():
    generated = render_schema()
    assert generated.startswith("-- GENERATED from memo.models")
    committed_path = pathlib.Path(memo.models.__file__).parent.with_name("schema.sql")
    assert committed_path.read_text(encoding="utf-8") == generated
