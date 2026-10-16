"""Labeled retrieval benchmark for graph expansion (review issue #4 + classifier observation).

This is a *measurement* benchmark, not a tuning harness. It seeds a deterministic
multi-turn memory dataset (frozen clock, fallback embeddings, tmp DBs only) and
reports retrieval quality with graph expansion OFF versus ON. The fusion
coefficients are logged, never retuned.
Token overhead is a character proxy: summed len(value) of the returned top-10,
averaged per query (no tokenizer is run).

Measured table (frozen clock 2026-10-16T12:00:00-03:00, fallback embeddings,
13 fixed queries over 4 topics, labels inline below; graph OFF vs ON):

    fusion weights: vector=0.60 text=0.25 retention=0.15 (MEASURE-ONLY)
    metric                     OFF       ON      floor
    Recall@5                 0.369    0.692     ON >= OFF
    Recall@10                0.462    0.815     ON >= OFF
    nDCG@10                  0.528    0.809     ON >= OFF
    irrelevant rate@10       0.792    0.615     ON <= OFF + 0.15
    token overhead (chars/q)   270      281     report-only
    latency median (ms)       16.0     26.0      ON <= 3x OFF
    temporal errors             0        0      == 0 both
    contradiction errors        0        0      ON == 0

    classifier (temporal.classify + is_trivia), 27 labeled cases:
      unrelated 5/5  qualifies 5/5  extends 5/5  contradicts 5/5
      reasserts 4/4  trivia 3/3      overall 27/27 = 1.000

Why expansion helps here: expansion scores a neighbor as source_rank x edge_weight
x 0.6, so it can only lift a related fact whose *own* fused score is low. The
dataset therefore ages every fact uniformly (BACKDATE_DAYS) so the lexically
matched anchor stands out by its text term while its graph neighbors sit in the
same low band as unrelated filler; expansion then lifts the neighbors above the
filler. This is the honest regime in which the feature has an effect.

Dataset: 4 topics (homelab, frontend, study, garden), each an anchor fact plus 4
related children (derived_from, confirmed by two declarers), one expired fact
(past valid_until) and one future-dated fact (future valid_from) whose values
match the query token so a broken validity filter surfaces them. Plus one
superseded pair (produced by the save ladder) and one declared contradiction
pair carrying an alias. Filler keys are lexically distinct so the FUZZ_HIT=80
matcher never merges them; test_dataset_integrity pins the exact key set.
"""
from __future__ import annotations

import datetime
import math
import shutil
import time

import pytest

from memo import db, embeddings, rank, temporal
from memo.memory.common import is_trivia
from memo.memory import search as search_module
from memo.memory.save import save
from memo.memory.search import search

FROZEN_NOW = "2026-10-16T12:00:00-03:00"
BACKDATE_DAYS = 30
LIMIT = 10
FUSION = (rank.WEIGHT_VECTOR, rank.WEIGHT_TEXT, rank.WEIGHT_RETENTION)

TOPICS = {
    "homelab": {
        "query": "quokka",
        "anchor": ("homelab.build.plan", "homelab build plan uses quokka rack"),
        "children": [
            ("homelab.rack.layout", "rack unit layout for the cabinet"),
            ("homelab.network.vlan", "vlan segmentation for lab traffic"),
            ("homelab.power.backup", "battery backup sizing for the rack"),
            ("homelab.cooling.airflow", "airflow plan for the closet"),
        ],
        "expired": ("homelab.warranty.lapsed", "old quokka lease detail", "2026-09-01"),
        "future": ("homelab.plan.pending", "planned quokka upgrade detail", "2027-01-01"),
    },
    "frontend": {
        "query": "zephyr",
        "anchor": ("frontend.app.stack", "frontend app stack uses zephyr vite react"),
        "children": [
            ("frontend.build.tooling", "build tooling pipeline for the bundle"),
            ("frontend.style.system", "style system tokens for the theme"),
            ("frontend.state.store", "state store choice for the app"),
            ("frontend.test.runner", "test runner setup for components"),
        ],
        "expired": ("frontend.trial.lapsed", "old zephyr lease detail", "2026-09-01"),
        "future": ("frontend.rollout.pending", "planned zephyr upgrade detail", "2027-01-01"),
    },
    "study": {
        "query": "nimbus",
        "anchor": ("study.plan.goal", "study plan goal nimbus olympiad prep"),
        "children": [
            ("study.math.topics", "math topics for the exam"),
            ("study.ml.curriculum", "machine learning topics for the exam"),
            ("study.schedule.weekly", "weekly schedule for practice"),
            ("study.mock.cadence", "mock test cadence for practice"),
        ],
        "expired": ("study.voucher.lapsed", "old nimbus lease detail", "2026-09-01"),
        "future": ("study.enroll.pending", "planned nimbus upgrade detail", "2027-01-01"),
    },
    "garden": {
        "query": "marlin",
        "anchor": ("garden.plot.plan", "garden plot plan marlin tomatoes"),
        "children": [
            ("garden.soil.mix", "soil mix for the beds"),
            ("garden.water.schedule", "watering schedule for summer"),
            ("garden.seed.orders", "seed orders for spring"),
            ("garden.pest.control", "pest control for the plot"),
        ],
        "expired": ("garden.frost.lapsed", "old marlin lease detail", "2026-09-01"),
        "future": ("garden.harvest.pending", "planned marlin upgrade detail", "2027-01-01"),
    },
}

SUPERSEDED = ("study.ups.size.v1", "nimbus ups size 1500va",
              "study.ups.size.v2", "nimbus ups size now 2200va instead")
CONTRADICTION = ("frontend.theme.dark", "frontend theme dark palette",
                 "frontend.canvas.light", "frontend theme light palette")
CONTRADICTION_ALIAS = {"segment": "frontend.palette", "canonical": "frontend.theme"}

NOISE_KEYS = ["noise.quilt", "noise.raven", "noise.saddle", "noise.violin",
              "noise.walnut", "noise.yarrow", "noise.ember", "noise.cobalt",
              "noise.dune", "noise.flint"]


def topic_current_keys(topic):
    spec = TOPICS[topic]
    return {spec["anchor"][0]} | {key for key, _ in spec["children"]}


def topic_distractors(topic):
    distractors = set(NOISE_KEYS) | {CONTRADICTION[0], CONTRADICTION[2]}
    for other in TOPICS:
        if other != topic:
            distractors |= topic_current_keys(other)
    return distractors


def build_queries():
    queries = [(TOPICS[topic]["query"], topic_current_keys(topic),
                topic_distractors(topic)) for topic in TOPICS]
    for query, topic in (("rack", "homelab"), ("vlan", "homelab"),
                         ("cabinet", "homelab"), ("bundle", "frontend"),
                         ("exam", "study"), ("olympiad", "study"),
                         ("summer", "garden"), ("tomatoes", "garden")):
        queries.append((query, topic_current_keys(topic), topic_distractors(topic)))
    queries.append(("palette", {CONTRADICTION[0], CONTRADICTION[2]},
                    set(NOISE_KEYS) | topic_current_keys("homelab")
                    | topic_current_keys("study") | topic_current_keys("garden")))
    return queries


QUERIES = build_queries()

CLASSIFIER_CASES = [
    ("coffee grinder settings fine", "also garden seeds arrived", "unrelated"),
    ("budget spreadsheet updated", "some hiking boots ordered", "unrelated"),
    ("database backup nightly", "also marathon training starts", "unrelated"),
    ("car insurance renewed", "too many plants bought", "unrelated"),
    ("kitchen remodel planned", "as well as guitar lessons", "unrelated"),
    ("homelab rack layout set", "also rack rails added", "qualifies"),
    ("study plan weekly set", "also weekly math drills", "qualifies"),
    ("frontend theme tokens ready", "too theme contrast tweaked", "qualifies"),
    ("garden soil mix ready", "some soil compost added", "qualifies"),
    ("server cooling airflow good", "also cooling fan swapped", "qualifies"),
    ("study plan covers math", "study plan covers physics", "extends"),
    ("editor uses vim bindings", "editor uses emacs bindings", "extends"),
    ("deploy target staging server", "deploy target production server", "extends"),
    ("database runs postgres", "database runs mysql", "extends"),
    ("frontend bundle config", "frontend bundle config documented", "extends"),
    ("theme mode dark", "actually theme mode light", "contradicts"),
    ("deploy target staging", "switched deploy target to production", "contradicts"),
    ("editor is vim", "editor is not vim anymore", "contradicts"),
    ("budget monthly 500", "budget monthly changed to 800", "contradicts"),
    ("prefer tea over coffee", "prefer coffee over tea", "contradicts"),
    ("deploy target staging", "still deploy target staging", "reasserts"),
    ("homelab plan approved", "confirms homelab plan approved", "reasserts"),
    ("study schedule weekly", "remains study schedule weekly", "reasserts"),
    ("garden soil ready", "continues to use garden soil", "reasserts"),
    ("thanks", "", "trivia"),
    ("ok", "", "trivia"),
    ("good morning", "", "trivia"),
]


def confirmed_link(conn, src, dst, value):
    save(conn, src, value, links=[{"to": dst, "rel": "derived_from"}],
         agent_id="A" * 8)
    save(conn, src, value, links=[{"to": dst, "rel": "derived_from"}],
         agent_id="B" * 8)


def backdate(conn, keys, days):
    when = (datetime.datetime.fromisoformat(FROZEN_NOW)
            - datetime.timedelta(days=days)).isoformat()
    conn.execute("UPDATE memories SET created_at=? WHERE key IN (%s)"
                 % ",".join("?" * len(keys)), (when, *keys))
    conn.commit()


def build(conn):
    keys = []
    for spec in TOPICS.values():
        anchor_key, anchor_value = spec["anchor"]
        save(conn, anchor_key, anchor_value)
        keys.append(anchor_key)
        for child_key, child_value in spec["children"]:
            confirmed_link(conn, child_key, anchor_key, child_value)
            keys.append(child_key)
        expired_key, expired_value, expired_until = spec["expired"]
        save(conn, expired_key, expired_value, valid_until=expired_until)
        keys.append(expired_key)
        future_key, future_value, future_from = spec["future"]
        save(conn, future_key, future_value, valid_from=future_from)
        keys.append(future_key)
    save(conn, SUPERSEDED[0], SUPERSEDED[1])
    save(conn, SUPERSEDED[2], SUPERSEDED[3])
    keys += [SUPERSEDED[0], SUPERSEDED[2]]
    save(conn, CONTRADICTION[0], CONTRADICTION[1])
    save(conn, CONTRADICTION[2], CONTRADICTION[3],
         links=[{"to": CONTRADICTION[0], "rel": "contradicts"}],
         aliases=[CONTRADICTION_ALIAS])
    keys += [CONTRADICTION[0], CONTRADICTION[2]]
    for key in NOISE_KEYS:
        save(conn, key, f"misc filler {key.split('.')[-1]}")
        keys.append(key)
    backdate(conn, keys, BACKDATE_DAYS)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    saved_now, saved_fallback = temporal.now_iso, embeddings.force_fallback
    temporal.now_iso = lambda: FROZEN_NOW
    embeddings.force_fallback = True
    path = tmp_path_factory.mktemp("bench") / "template.db"
    conn = db.connect(str(path))
    assert db.migrate(conn) == 3
    build(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    db._db_cache.clear()
    try:
        yield path
    finally:
        temporal.now_iso = saved_now
        embeddings.force_fallback = saved_fallback


def run_query(template, query, expanded, work_path):
    shutil.copyfile(str(template), str(work_path))
    conn = db.connect(str(work_path))
    original = search_module.expand_candidates
    if not expanded:
        search_module.expand_candidates = lambda conn, scored, *args, **kwargs: scored
    try:
        started = time.perf_counter()
        hits = search(conn, query, limit=LIMIT)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
    finally:
        search_module.expand_candidates = original
    return conn, hits, elapsed_ms


def row_status(conn, key):
    return conn.execute(
        "SELECT status, valid_until FROM memories"
        " WHERE key=? AND namespace='default'", (key,)).fetchone()


def has_contradiction_edge(conn, key):
    return conn.execute(
        "SELECT 1 FROM edges WHERE rel='contradicts' AND namespace='default'"
        " AND (src_key=? OR dst_key=?) LIMIT 1", (key, key)).fetchone() is not None


def ndcg_at_10(keys, relevant):
    dcg = sum(1.0 / math.log2(i + 2) for i, key in enumerate(keys[:10]) if key in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    return dcg / ideal if ideal else 0.0


def evaluate(bench, tmp_path, flag, tag):
    rec5 = rec10 = ndcg = irr = 0.0
    tokens = 0
    latencies = []
    temporal_errors = []
    contradiction_errors = []
    for index, (query, relevant, _distractors) in enumerate(QUERIES):
        conn, hits, elapsed_ms = run_query(
            bench, query, flag, tmp_path / f"{tag}_{index}.db")
        latencies.append(elapsed_ms)
        keys = [hit["key"] for hit in hits]
        rec5 += len(set(keys[:5]) & relevant) / len(relevant)
        rec10 += len(set(keys[:10]) & relevant) / len(relevant)
        ndcg += ndcg_at_10(keys, relevant)
        irr += len([k for k in keys[:10] if k not in relevant]) / max(1, len(keys[:10]))
        tokens += sum(len(hit["value"]) for hit in hits[:10])
        for hit in hits:
            meta = row_status(conn, hit["key"])
            if meta is not None:
                status, valid_until = meta
                if status != "current" or (valid_until and valid_until <= FROZEN_NOW):
                    temporal_errors.append((query, hit["key"]))
            if has_contradiction_edge(conn, hit["key"]) and not hit.get("contradictions"):
                contradiction_errors.append((query, hit["key"]))
        conn.close()
    count = len(QUERIES)
    latencies.sort()
    return {
        "rec5": rec5 / count, "rec10": rec10 / count, "ndcg": ndcg / count,
        "irr": irr / count, "tokens": tokens / count,
        "latency": latencies[len(latencies) // 2],
        "temporal_errors": temporal_errors, "contradiction_errors": contradiction_errors,
    }


def format_table(off, on, classifier_text):
    lines = [
        "memo retrieval benchmark (graph OFF vs ON)",
        f"frozen clock {FROZEN_NOW} | fallback embeddings |"
        f" {len(QUERIES)} queries over {len(TOPICS)} topics",
        f"fusion weights: vector={FUSION[0]} text={FUSION[1]}"
        f" retention={FUSION[2]} (MEASURE-ONLY, not retuned)",
        "",
        f"{'metric':<26}{'OFF':>10}{'ON':>10}",
        f"{'Recall@5':<26}{off['rec5']:>10.3f}{on['rec5']:>10.3f}",
        f"{'Recall@10':<26}{off['rec10']:>10.3f}{on['rec10']:>10.3f}",
        f"{'nDCG@10':<26}{off['ndcg']:>10.3f}{on['ndcg']:>10.3f}",
        f"{'irrelevant rate@10':<26}{off['irr']:>10.3f}{on['irr']:>10.3f}",
        f"{'token overhead (chars/q)':<26}{off['tokens']:>10.0f}{on['tokens']:>10.0f}",
        f"{'latency median (ms)':<26}{off['latency']:>10.1f}{on['latency']:>10.1f}",
        f"{'temporal errors':<26}{len(off['temporal_errors']):>10}"
        f"{len(on['temporal_errors']):>10}",
        f"{'contradiction errors':<26}{len(off['contradiction_errors']):>10}"
        f"{len(on['contradiction_errors']):>10}",
        "",
        classifier_text,
    ]
    return "\n".join(lines)


def classifier_report():
    per_class = {}
    confusions = []
    for old, new, expected in CLASSIFIER_CASES:
        if expected == "trivia":
            got = "trivia" if is_trivia(new) else "not_trivia"
        else:
            got = temporal.classify(old, "benchmark.key", new)
        correct, total = per_class.get(expected, (0, 0))
        per_class[expected] = (correct + int(got == expected), total + 1)
        if got != expected:
            confusions.append((expected, got, new))
    lines = ["classifier accuracy (temporal.classify + is_trivia, labeled cases)"]
    for name in sorted(per_class):
        correct, total = per_class[name]
        lines.append(f"  {name:<12} {correct}/{total} = {correct / total:.3f}")
    overall = sum(correct for correct, _ in per_class.values())
    accuracy = overall / len(CLASSIFIER_CASES)
    lines.append(f"  {'overall':<12} {overall}/{len(CLASSIFIER_CASES)} = {accuracy:.3f}")
    if confusions:
        lines.append(f"  confusions: {confusions}")
    return "\n".join(lines), accuracy, confusions


def test_benchmark_table_and_floors(bench, tmp_path):
    off = evaluate(bench, tmp_path, False, "off")
    on = evaluate(bench, tmp_path, True, "on")
    classifier_text, accuracy, confusions = classifier_report()
    table = format_table(off, on, classifier_text)
    print("\n" + table + "\n")
    assert on["rec10"] >= off["rec10"], table
    assert on["rec5"] >= off["rec5"], table
    assert on["ndcg"] >= off["ndcg"], table
    assert on["irr"] <= off["irr"] + 0.15, table
    assert off["temporal_errors"] == [], table
    assert on["temporal_errors"] == [], table
    assert on["contradiction_errors"] == [], table
    assert on["latency"] <= 3 * off["latency"], table
    assert accuracy >= 0.8, table + f"\nconfusions: {confusions}"


def test_dataset_integrity(bench):
    conn = db.connect(str(bench))
    rows = {row[0] for row in conn.execute(
        "SELECT key FROM memories WHERE namespace='default'")}
    conn.close()
    expected = set()
    for spec in TOPICS.values():
        expected |= {spec["anchor"][0], spec["expired"][0], spec["future"][0]}
        expected |= {key for key, _ in spec["children"]}
    expected |= {SUPERSEDED[0], SUPERSEDED[2], CONTRADICTION[0], CONTRADICTION[2]}
    expected |= set(NOISE_KEYS)
    assert rows == expected, sorted(expected - rows) + sorted(rows - expected)
    assert len(QUERIES) == 13
    for query, relevant, distractors in QUERIES:
        assert relevant & distractors == set(), (query, relevant & distractors)
        assert relevant <= expected, (query, relevant - expected)


def test_contradiction_pair_surfaces_flagged(bench, tmp_path):
    conn, hits, _ = run_query(bench, "palette", True, tmp_path / "palette.db")
    flagged = {hit["key"]: hit.get("contradictions") for hit in hits}
    conn.close()
    for key in CONTRADICTION[0], CONTRADICTION[2]:
        assert key in flagged, flagged
        assert flagged[key], flagged
    off_conn, off_hits, _ = run_query(bench, "palette", False, tmp_path / "palette_off.db")
    off_flagged = {hit["key"]: hit.get("contradictions") for hit in off_hits}
    off_conn.close()
    for key in CONTRADICTION[0], CONTRADICTION[2]:
        assert key in off_flagged and off_flagged[key], off_flagged


def test_expired_and_future_facts_are_never_returned(bench, tmp_path):
    conn, hits, _ = run_query(bench, "quokka", True, tmp_path / "temporal.db")
    keys = {hit["key"] for hit in hits}
    statuses = {key: conn.execute(
        "SELECT status FROM memories WHERE key=? AND namespace='default'",
        (key,)).fetchone()[0] for key in (SUPERSEDED[0], SUPERSEDED[2])}
    conn.close()
    assert "homelab.warranty.lapsed" not in keys
    assert "homelab.plan.pending" not in keys
    assert SUPERSEDED[0] not in keys
    assert statuses[SUPERSEDED[0]] == "superseded"
    assert statuses[SUPERSEDED[2]] == "current"
    conn, nimbus_hits, _ = run_query(bench, "nimbus", True, tmp_path / "superseded.db")
    nimbus_keys = {hit["key"] for hit in nimbus_hits}
    conn.close()
    assert SUPERSEDED[2] in nimbus_keys
    assert SUPERSEDED[0] not in nimbus_keys
