import sys

sys.path.insert(0, "src")
from peewee import FloatField, IntegerField, Model, SQL, SqliteDatabase, TextField

db = SqliteDatabase(":memory:")


class T(Model):
    ns = TextField(default="default", constraints=[SQL("DEFAULT 'default'")])
    sc = FloatField(default=1.0, constraints=[SQL("DEFAULT 1.0")])
    us = IntegerField(default=0, constraints=[SQL("DEFAULT 0")])
    st = TextField(default="current", constraints=[SQL("DEFAULT 'current'")])

    class Meta:
        database = db
        table_name = "t"


db.create_tables([T])
print(db.execute_sql("SELECT sql FROM sqlite_master WHERE name='t'").fetchone()[0])
