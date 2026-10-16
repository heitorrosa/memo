import datetime
import json

import pytest

from memo import db, embeddings, temporal
from memo.memory import search as search_module
from memo.memory.save import save
from memo.memory.search import search

FROZEN_NOW = "2026-10-16T12:00:00-03:00"

TOPICS = ("homelab", "kitchen", "travel", "garden", "studio",
          "garage", "pantry", "camera", "coffee", "network")
ANCHOR_TOKENS = ("quokka", "zephyr", "nimbus", "marlin", "cobalt",
                 "tundra", "velvet", "onyx", "ember", "harbor")
CHILD_WORDS = ("rackunit", "switchunit", "kettleunit", "ovenunit",
               "trainunit", "hotelunit", "spadeunit", "rakeunit",
               "micunit", "mixerunit", "oilunit", "brakeunit",
               "lumbarunit", "armrestunit", "primeunit", "zoomunit",
               "roastunit", "grindunit", "firmwareunit", "ssidunit")
FILLER_WORDS = ("filleralfa", "fillerbravo", "fillercharlie", "fillerdelta",
                "fillerecho", "fillerfoxtrot", "fillergolf", "fillerhotel",
                "fillerindia", "fillerjuliet", "fillerkilo", "fillerlima")
CAP_WORDS = ("capalfa", "capbravo", "capcharlie", "capdelta", "capecho",
             "capfoxtrot", "capgolf", "caphotel", "capindia", "capjuliet",
             "capkilo", "caplima", "capmike", "capnovember", "caposcar",
             "cappapa", "capquebec", "capromeo", "capsierra", "captango",
             "capuniform", "capvictor", "capwhiskey", "capxray", "capyankee")
CONTRADICT_A = "lab.theme.dark"
CONTRADICT_B = "lab.glare.mode"
ISOLATED = "isolatednote"
LAB_QUERY = "display"
ISOLATED_QUERY = "standalone"


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    monkeypatch.setattr(temporal, "now_iso", lambda: FROZEN_NOW)


def anchor_key(topic):
    return f"{topic}.memory.anchor"


def child_keys(topic):
    return (f"{topic}.rack.unit", f"{topic}.switch.unit")


def confirmed_link(conn, src, dst, value):
    save(conn, src, value, links=[{"to": dst, "rel": "derived_from"}],
         agent_id="A" * 8)
    save(conn, src, value, links=[{"to": dst, "rel": "derived_from"}],
         agent_id="B" * 8)


def backdate(conn, keys, days):
    when = (datetime.datetime.fromisoformat(FROZEN_NOW)
            - datetime.timedelta(days=days)).isoformat()
    conn.execute(
        "UPDATE memories SET created_at=? WHERE key IN (%s)"
        % ",".join("?" * len(keys)), (when, *keys))
    conn.commit()


def seed(conn, fresh_key=None):
    old = [CONTRADICT_A, CONTRADICT_B, ISOLATED]
    index = 0
    for topic, token in zip(TOPICS, ANCHOR_TOKENS):
        one, two = child_keys(topic)
        save(conn, anchor_key(topic), f"also {token}")
        confirmed_link(conn, one, anchor_key(topic), f"also {CHILD_WORDS[index]}")
        confirmed_link(conn, two, anchor_key(topic), f"also {CHILD_WORDS[index + 1]}")
        index += 2
        old += [anchor_key(topic), one, two]
    save(conn, CONTRADICT_A, f"also {LAB_QUERY} dark")
    save(conn, CONTRADICT_B, f"also {LAB_QUERY} light",
         links=[{"to": CONTRADICT_A, "rel": "contradicts"}])
    save(conn, ISOLATED, f"also {ISOLATED_QUERY}")
    fillers = [f"filler.note.{n:02d}" for n in range(len(FILLER_WORDS))]
    for key, word in zip(fillers, FILLER_WORDS):
        save(conn, key, f"also {word}")
    if fresh_key is not None:
        old = [key for key in old if key != fresh_key]
    backdate(conn, old, 60)
    backdate(conn, fillers, 3)
    return fillers


def run_query(tmp_path, name, query, expanded=True, fresh_key=None, limit=10):
    conn = db.connect(str(tmp_path / f"{name}.db"))
    assert db.migrate(conn) == 3
    seed(conn, fresh_key=fresh_key)
    original = search_module.expand_candidates
    if not expanded:
        search_module.expand_candidates = lambda conn, scored, *args, **kwargs: scored
    try:
        hits = search(conn, query, limit=limit)
    finally:
        search_module.expand_candidates = original
        conn.close()
    return hits


def test_expansion_invoked_by_default(tmp_path, monkeypatch):
    calls = []
    original = search_module.expand_candidates
    monkeypatch.setattr(
        search_module, "expand_candidates",
        lambda *args, **kwargs: calls.append(1) or original(*args, **kwargs))
    run_query(tmp_path, "on_spy", ANCHOR_TOKENS[0],
              fresh_key=anchor_key(TOPICS[0]))
    assert calls == [1]


def test_expansion_is_deterministic(tmp_path):
    first = run_query(tmp_path, "on_a", ANCHOR_TOKENS[0],
                      fresh_key=anchor_key(TOPICS[0]))
    second = run_query(tmp_path, "on_b", ANCHOR_TOKENS[0],
                       fresh_key=anchor_key(TOPICS[0]))
    assert json.dumps(first) == json.dumps(second)


def test_expansion_co_retrieval_rate(tmp_path):
    rows = []
    off_hits = on_hits = 0
    for topic, token in zip(TOPICS, ANCHOR_TOKENS):
        related = {anchor_key(topic), *child_keys(topic)}
        off_keys = {hit["key"] for hit in run_query(
            tmp_path, f"off_{topic}", token, False, fresh_key=anchor_key(topic))}
        on_keys = {hit["key"] for hit in run_query(
            tmp_path, f"on_{topic}", token, True, fresh_key=anchor_key(topic))}
        off_ok = len(related & off_keys) >= 2
        on_ok = len(related & on_keys) >= 2
        off_hits += off_ok
        on_hits += on_ok
        rows.append(f"| {token} | {int(off_ok)} | {int(on_ok)} |")
    table = "\n".join(rows)
    assert on_hits >= off_hits, table
    assert on_hits >= 8, table
    assert off_hits < on_hits, table
    assert (off_hits, on_hits) == (0, len(TOPICS)), table


def test_contradicts_pair_present_but_never_scored(tmp_path):
    off = run_query(tmp_path, "off_lab", LAB_QUERY, False)
    on = run_query(tmp_path, "on_lab", LAB_QUERY, True)
    off_scores = {hit["key"]: hit["score"] for hit in off}
    on_scores = {hit["key"]: hit["score"] for hit in on}
    for key in (CONTRADICT_A, CONTRADICT_B):
        assert key in off_scores and key in on_scores
        assert on_scores[key] == pytest.approx(off_scores[key])


def test_isolated_fact_is_unaffected(tmp_path):
    off = run_query(tmp_path, "off_iso", ISOLATED_QUERY, False)
    on = run_query(tmp_path, "on_iso", ISOLATED_QUERY, True)
    off_scores = {hit["key"]: hit["score"] for hit in off}
    on_scores = {hit["key"]: hit["score"] for hit in on}
    assert ISOLATED in off_scores and ISOLATED in on_scores
    assert on_scores[ISOLATED] == pytest.approx(off_scores[ISOLATED])
    assert set(off_scores) == set(on_scores)


def test_expansion_caps_merged_neighbors_at_twenty(tmp_path):
    conn = db.connect(str(tmp_path / "cap.db"))
    assert db.migrate(conn) == 3
    hub = "hub.memory.anchor"
    save(conn, hub, "also hubtoken")
    children = [f"hub.child.{n:02d}" for n in range(len(CAP_WORDS))]
    for key, word in zip(children, CAP_WORDS):
        confirmed_link(conn, key, hub, f"also {word}")
    backdate(conn, children, 60)
    hits = search(conn, "hubtoken", limit=40)
    conn.close()
    child_set = set(children)
    boosted = [hit for hit in hits
               if hit["key"] in child_set and hit["score"] > 0.35]
    assert len(boosted) == 20


def test_expansion_improves_co_retrieval(tmp_path):
    related = {anchor_key(TOPICS[0]), *child_keys(TOPICS[0])}
    off_keys = {hit["key"] for hit in run_query(
        tmp_path, "off_default_cmp", ANCHOR_TOKENS[0], False,
        fresh_key=anchor_key(TOPICS[0]))}
    default_keys = {hit["key"] for hit in run_query(
        tmp_path, "on_default_cmp", ANCHOR_TOKENS[0], True,
        fresh_key=anchor_key(TOPICS[0]))}
    assert len(related & off_keys) < len(related & default_keys)
    assert len(related & default_keys) >= 2


WINDOW_ANCHOR = "windowanchor"
WINDOW_CHILD = "windowchild"
WINDOW_OLD = "windowold"
WINDOW_FUTURE = "windowfuture"
OPEN_SINCE = "2026-01-01"
PAST_WINDOW = "2026-05-01"
MID_WINDOW = "2026-01-15"
KNOWN_AT = "2026-12-01"
LATER_WINDOW = "2027-06-01"


def window_conn(tmp_path, name):
    conn = db.connect(str(tmp_path / f"{name}.db"))
    assert db.migrate(conn) == 3
    return conn


def window_hits(conn, query, valid_at=None, as_known_at=None, expanded=True,
                limit=10):
    original = search_module.expand_candidates
    if not expanded:
        search_module.expand_candidates = lambda conn, scored, *args, **kwargs: scored
    try:
        kwargs = {}
        if valid_at is not None:
            kwargs["valid_at"] = valid_at
        if as_known_at is not None:
            kwargs["as_known_at"] = as_known_at
        return search(conn, query, limit=limit, **kwargs)
    finally:
        search_module.expand_candidates = original


def window_link(conn, key, value, parent, valid_from=None, valid_until=None):
    for declarer in ("A" * 8, "B" * 8):
        save(conn, key, value, links=[{"to": parent, "rel": "derived_from"}],
             agent_id=declarer, valid_from=valid_from, valid_until=valid_until)


def scores(hits):
    return {hit["key"]: hit["score"] for hit in hits}


def test_expansion_honors_valid_at_window(tmp_path):
    conn = window_conn(tmp_path, "expand_window_past")
    save(conn, "lab.anchor", f"also {WINDOW_ANCHOR}", valid_from=OPEN_SINCE)
    window_link(conn, "lab.child", f"also {WINDOW_CHILD}", "lab.anchor")
    backdate(conn, ["lab.child"], 60)
    past_on = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=PAST_WINDOW))
    past_off = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=PAST_WINDOW,
                                  expanded=False))
    now_on = scores(window_hits(conn, WINDOW_ANCHOR))
    now_off = scores(window_hits(conn, WINDOW_ANCHOR, expanded=False))
    conn.close()
    assert "lab.anchor" in past_on
    assert "lab.child" not in past_on
    assert set(past_on) == set(past_off)
    assert now_on["lab.child"] > now_off["lab.child"]


def test_expansion_honors_past_only_neighbor(tmp_path):
    conn = window_conn(tmp_path, "expand_window_old")
    save(conn, "lab.anchor", f"also {WINDOW_ANCHOR}", valid_from=OPEN_SINCE)
    window_link(conn, "lab.old", f"also {WINDOW_OLD}", "lab.anchor",
                valid_from=OPEN_SINCE, valid_until="2026-02-01")
    backdate(conn, ["lab.old"], 60)
    historical_on = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=MID_WINDOW))
    historical_off = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=MID_WINDOW,
                                        expanded=False))
    present = scores(window_hits(conn, WINDOW_ANCHOR))
    conn.close()
    assert historical_on["lab.old"] > historical_off["lab.old"]
    assert "lab.old" not in present


def test_expansion_honors_as_known_at(tmp_path):
    conn = window_conn(tmp_path, "expand_window_known")
    save(conn, "lab.anchor", f"also {WINDOW_ANCHOR}", valid_from=OPEN_SINCE)
    window_link(conn, "lab.future", f"also {WINDOW_FUTURE}", "lab.anchor",
                valid_from="2027-01-01")
    backdate(conn, ["lab.future"], 60)
    known = scores(window_hits(conn, WINDOW_ANCHOR, as_known_at=KNOWN_AT))
    windowed = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=KNOWN_AT))
    later_on = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=LATER_WINDOW))
    later_off = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=LATER_WINDOW,
                                   expanded=False))
    conn.close()
    assert "lab.anchor" in known
    assert "lab.future" not in known
    assert set(known) == set(windowed)
    assert later_on["lab.future"] > later_off["lab.future"]


def test_expansion_time_precedence(tmp_path):
    conn = window_conn(tmp_path, "expand_window_precedence")
    save(conn, "lab.anchor", f"also {WINDOW_ANCHOR}", valid_from=OPEN_SINCE)
    window_link(conn, "lab.future", f"also {WINDOW_FUTURE}", "lab.anchor",
                valid_from="2027-01-01")
    backdate(conn, ["lab.future"], 60)
    implicit = scores(window_hits(conn, WINDOW_ANCHOR, as_known_at=KNOWN_AT))
    explicit_on = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=LATER_WINDOW,
                                     as_known_at=KNOWN_AT))
    explicit_off = scores(window_hits(conn, WINDOW_ANCHOR, valid_at=LATER_WINDOW,
                                      as_known_at=KNOWN_AT, expanded=False))
    conn.close()
    assert "lab.future" not in implicit
    assert explicit_on["lab.future"] > explicit_off["lab.future"]
