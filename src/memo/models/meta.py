from __future__ import annotations

from peewee import Model, TextField

from memo.models.orm import database


class MemoMeta(Model):
    key = TextField(column_name="k", primary_key=True)
    value = TextField(column_name="v")

    class Meta:
        table_name = "memo_meta"
        database = database
