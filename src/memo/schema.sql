-- GENERATED from memo.models: do not hand-edit. Edit src/memo/models/*.py, then run `python -m memo.models`.
CREATE TABLE IF NOT EXISTS memo_meta(
  k TEXT PRIMARY KEY, v TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS memories(
  key TEXT, namespace TEXT DEFAULT 'default',
  value TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('preference','analysis','feedback','context')),
  embedding BLOB NOT NULL,               -- 384 float32, all-MiniLM-L6-v2
  stability REAL NOT NULL,               -- INITIAL_STABILITY: pref 14 / feedback 10 / analysis 7 / context 3
  score REAL DEFAULT 1.0, uses INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,              -- TRANSACTION time: when memo learned it. ISO-8601 with offset.
  updated_at TEXT NOT NULL,              -- last system touch. ISO-8601 with offset.
  valid_from TEXT NOT NULL,              -- VALID time start: when true of the user. May be FUTURE-dated.
  valid_to TEXT,                         -- VALID time end, NULL = open. Set on supersession: valid_to(A) = valid_from(B).
  status TEXT DEFAULT 'current' CHECK(status IN ('current','superseded')),
  family_id TEXT NOT NULL,               -- stable proposition ID; all versions of one fact share it.
  superseded_by TEXT,                    -- key of the winning row (forward link); NULL while current.
  relation_type TEXT CHECK(relation_type IN ('supersedes','corrects','qualifies')),
  PRIMARY KEY(key, namespace));
CREATE INDEX IF NOT EXISTS idx_memories_family ON memories(family_id, valid_from);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, namespace);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(key, value, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);
END;
CREATE TABLE IF NOT EXISTS archived_memories AS SELECT *, '' AS archived_at, '' AS archived_reason FROM memories LIMIT 0;
CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY, summary TEXT, source_count INT, rolled_up_at TEXT,
  started_at TEXT NOT NULL,              -- observation interval start.
  ended_at TEXT NOT NULL);               -- observation interval end; never used as valid-time evidence.
CREATE TABLE IF NOT EXISTS skills(
  name TEXT PRIMARY KEY, description TEXT NOT NULL, body TEXT NOT NULL,
  hash TEXT NOT NULL, version INTEGER DEFAULT 1, source TEXT DEFAULT 'local',
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
