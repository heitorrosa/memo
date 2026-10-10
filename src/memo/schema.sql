-- GENERATED from memo.models: do not hand-edit. Edit src/memo/models/{memories,skills,meta,agents}.py, then run `python -m memo.models`.
CREATE TABLE "agents" ("agent_id" TEXT NOT NULL PRIMARY KEY, "harness" TEXT NOT NULL, "capabilities" TEXT NOT NULL DEFAULT '[]', "first_seen" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "last_seen" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
CREATE TABLE "agent_state" ("agent_id" TEXT NOT NULL, "skill" TEXT NOT NULL, "hash" TEXT NOT NULL, "version" INTEGER NOT NULL DEFAULT 1, "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), PRIMARY KEY ("agent_id", "skill"), FOREIGN KEY ("agent_id") REFERENCES "agents" ("agent_id") ON DELETE CASCADE);
CREATE INDEX "agentstate_agent_id" ON "agent_state" ("agent_id");
CREATE TABLE "episodes" ("id" INTEGER NOT NULL PRIMARY KEY, "summary" TEXT, "source_count" INTEGER, "rolled_up_at" TEXT, "started_at" TEXT NOT NULL, "ended_at" TEXT NOT NULL);
CREATE TABLE "memo_meta" ("k" TEXT NOT NULL PRIMARY KEY, "v" TEXT NOT NULL);
CREATE TABLE "memories" ("key" TEXT NOT NULL, "namespace" TEXT NOT NULL DEFAULT 'default', "value" TEXT NOT NULL, "type" TEXT NOT NULL, "embedding" BLOB NOT NULL, "stability" REAL NOT NULL, "score" REAL NOT NULL DEFAULT 1.0, "uses" INTEGER NOT NULL DEFAULT 0, "created_at" TEXT NOT NULL, "updated_at" TEXT NOT NULL, "valid_from" TEXT NOT NULL, "valid_to" TEXT, "status" TEXT NOT NULL DEFAULT 'current', "family_id" TEXT NOT NULL, "superseded_by" TEXT, "relation_type" TEXT, PRIMARY KEY ("key", "namespace"), CHECK (type IN ('preference','analysis','feedback','context')), CHECK (status IN ('current','superseded')), CHECK (relation_type IN ('supersedes','corrects','qualifies')));
CREATE INDEX "memory_family_id_valid_from" ON "memories" ("family_id", "valid_from");
CREATE INDEX "memory_status_namespace" ON "memories" ("status", "namespace");
CREATE VIRTUAL TABLE "memories_fts" USING fts5 ("key", "value", content=memories, content_rowid=rowid);
CREATE TABLE "skills" ("name" TEXT NOT NULL PRIMARY KEY, "description" TEXT NOT NULL, "body" TEXT NOT NULL, "hash" TEXT NOT NULL, "version" INTEGER NOT NULL DEFAULT 1, "source" TEXT NOT NULL DEFAULT 'local', "created_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "updated_at" TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, key, value) VALUES('delete', old.rowid, old.key, old.value);
  INSERT INTO memories_fts(rowid, key, value) VALUES (new.rowid, new.key, new.value);
END;
CREATE TABLE archived_memories(
  "key" TEXT,
  namespace TEXT,
  value TEXT,
  type TEXT,
  embedding,
  stability REAL,
  score REAL,
  uses INT,
  created_at TEXT,
  updated_at TEXT,
  valid_from TEXT,
  valid_to TEXT,
  status TEXT,
  family_id TEXT,
  superseded_by TEXT,
  relation_type TEXT,
  archived_at,
  archived_reason
);
