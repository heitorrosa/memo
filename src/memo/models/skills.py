"""Skills-domain table (spec §5). Thin re-export: canonical text lives in
memo.models.orm (Peewee-canonical); this keeps the models/*.py import path
stable for render_schema()."""
from __future__ import annotations

from memo.models.orm import SKILLS_DDL

__all__ = ["SKILLS_DDL"]
