import numpy as np
import pytest
from starlette.testclient import TestClient

from memo import db, embeddings
from memo.server import static_ui

AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture(autouse=True)
def fallback():
    embeddings.force_fallback = True
    try:
        yield
    finally:
        embeddings.force_fallback = False


def make_client(tmp_path):
    from memo.server.core import build_server
    from memo.server.http import build_http_app

    path = str(tmp_path / "memo.db")
    conn = db.connect(path)
    assert db.migrate(conn) == 3
    conn.close()
    server = build_server(path)
    client = TestClient(build_http_app(server, "tok"),
                        raise_server_exceptions=False)
    return client, server


@pytest.fixture
def api(tmp_path):
    client, server = make_client(tmp_path)
    try:
        yield client, server
    finally:
        client.close()
        server.memo_conn.close()


def save(client, **body):
    return client.post("/api/memories", headers=AUTH, json=body)


def two_memories(client):
    assert save(client, key="project.alpha", type="context",
                value="prefers tabs for the project").json()["action"] == "created"
    assert save(client, key="project.beta", type="context",
                value="uses windows for the project shell",
                links=[{"to": "project.alpha", "rel": "supports"}]
                ).json()["action"] == "created"


def test_auth_required(api):
    client, _ = api
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/stats",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/stats", headers=AUTH).status_code == 200


def test_health_gated_and_shaped(api):
    client, _ = api
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers=AUTH).json() == {
        "ok": True, "version": 3,
        "vec": "vec" if db.HAS_VEC else "bruteforce"}


def test_stats_shape(api):
    client, _ = api
    body = client.get("/api/stats", headers=AUTH).json()
    assert body["version"] == 3
    assert isinstance(body["ui"], bool)
    assert body["db_path"].endswith("memo.db")
    assert set(body["counts"]) == {
        "memories", "current", "superseded", "archived", "skills", "agents",
        "edges", "aliases", "episodes"}
    assert set(body["by_type"]) == {
        "preference", "analysis", "feedback", "context"}
    assert body["by_status"] == {"current": 0, "superseded": 0}
    assert body["by_source"] == {}
    assert body["namespaces"] == ["default"]
    assert body["last_activity"] is None

    two_memories(client)
    body = client.get("/api/stats", headers=AUTH).json()
    assert body["counts"]["memories"] == 2
    assert body["counts"]["current"] == 2
    assert body["counts"]["edges"] >= 3
    assert body["by_type"]["context"] == 2
    assert body["last_activity"] is not None


def test_memory_list_and_detail_and_delete(api):
    client, _ = api
    two_memories(client)

    listing = client.get("/api/memories", headers=AUTH).json()
    assert listing["total"] == 2
    assert listing["limit"] == 50 and listing["offset"] == 0
    assert {item["key"] for item in listing["items"]} == {
        "project.alpha", "project.beta"}
    assert all("degree" in item for item in listing["items"])

    filtered = client.get("/api/memories?type=context&status=current",
                          headers=AUTH).json()
    assert filtered["total"] == 2
    assert client.get("/api/memories?type=nonsense",
                      headers=AUTH).status_code == 400
    assert client.get("/api/memories?sort=bogus",
                      headers=AUTH).status_code == 400
    assert client.get("/api/memories?limit=abc",
                      headers=AUTH).status_code == 400

    hits = client.get("/api/memories?q=project", headers=AUTH).json()
    assert {item["key"] for item in hits["items"]} == {
        "project.alpha", "project.beta"}
    narrowed = client.get("/api/memories?q=project&type=context",
                          headers=AUTH).json()
    assert narrowed["total"] == 2

    detail = client.get("/api/memories/project.alpha", headers=AUTH).json()
    assert detail["memory"]["key"] == "project.alpha"
    assert detail["memory"]["namespace"] == "default"
    assert detail["memory"]["status"] == "current"
    assert detail["memory"]["degree"] >= 1
    assert detail["aliases"] == []
    assert detail["family"][0]["key"] == "project.alpha"
    edges = {(e["src_key"], e["dst_key"], e["rel"]): e for e in detail["edges"]}
    assert ("project.beta", "project.alpha", "supports") in edges
    assert edges[("project.beta", "project.alpha", "supports")]["direction"] == "in"
    assert all(e["direction"] in ("in", "out") for e in detail["edges"])
    assert any(d["rel"] == "supports" for d in detail["declarers"])

    assert client.get("/api/memories/ghost", headers=AUTH).status_code == 404

    assert client.delete("/api/memories/project.beta",
                         headers=AUTH).json() == {
        "key": "project.beta", "action": "deleted"}
    assert client.get("/api/memories/project.beta",
                      headers=AUTH).status_code == 404
    assert client.delete("/api/memories/project.beta",
                         headers=AUTH).json() == {
        "key": "project.beta", "action": "not_found"}


def test_memory_bad_input(api):
    client, _ = api
    assert save(client, key="x", value="a durable value",
                type="nonsense").status_code == 400
    assert save(client, key="x", type="context").status_code == 400
    assert save(client, value="a durable value",
                type="context").status_code == 400
    bad = client.post("/api/memories", headers=AUTH, content="{not json")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_input"


def test_skills_roundtrip(api):
    client, _ = api
    created = client.post("/api/skills", headers=AUTH, json={
        "name": "demo-skill", "description": "demo", "body": "step one"})
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1

    listing = client.get("/api/skills", headers=AUTH).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["name"] == "demo-skill"
    assert item["revisions"] == 1
    assert item["body_bytes"] == len("step one")
    assert client.get("/api/skills?q=ghost", headers=AUTH).json()["total"] == 0

    detail = client.get("/api/skills/demo-skill", headers=AUTH).json()
    assert detail["skill"]["body"] == "step one"
    assert detail["skill"]["source"] == "local"
    assert detail["skill"]["version"] == 1

    patched = client.post("/api/skills/demo-skill/revisions", headers=AUTH,
                          json={"patch": "more"})
    assert patched.json()["version"] == 2
    assert client.get("/api/skills/demo-skill",
                      headers=AUTH).json()["skill"]["version"] == 2
    old = client.get("/api/skills/demo-skill?version=1", headers=AUTH).json()
    assert old["skill"]["body"] == "step one"

    assert client.get("/api/skills/ghost", headers=AUTH).status_code == 404
    assert client.post("/api/skills", headers=AUTH,
                       json={"name": "x"}).status_code == 400


def test_agents(api):
    client, server = api
    assert client.get("/api/agents", headers=AUTH).json() == {"items": []}
    from memo.skills.sync import sync

    sync(server.memo_conn, "opencode", capabilities=["files"])
    items = client.get("/api/agents", headers=AUTH).json()["items"]
    assert len(items) == 1
    assert items[0]["harness"] == "opencode"
    assert items[0]["capabilities"] == ["files"]
    assert items[0]["skill_count"] == 0
    assert items[0]["skills"] == []


def test_graph_closed_under_nodes(api):
    client, _ = api
    two_memories(client)
    body = client.get("/api/graph", headers=AUTH).json()
    keys = {node["key"] for node in body["nodes"]}
    assert keys == {"project.alpha", "project.beta"}
    assert all(edge["src_key"] in keys and edge["dst_key"] in keys
               for edge in body["edges"])
    assert any(edge["rel"] == "supports" for edge in body["edges"])
    assert all(node["degree"] >= 1 for node in body["nodes"])
    assert all(node["key"] in keys for node in body["nodes"])

    capped = client.get("/api/graph?limit=1", headers=AUTH).json()
    assert len(capped["nodes"]) == 1
    assert capped["edges"] == []


def test_ui_mount_is_public(api):
    client, _ = api
    for path in ("/ui", "/ui/", "/ui/index.html"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code != 401, response.text


def test_ui_mount_serves_the_build_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("MEMO_UI_DIR", raising=False)
    if static_ui.ui_dir() is None:
        pytest.skip("no built dashboard in this worktree")
    client, server = make_client(tmp_path)
    try:
        for path in ("/ui/", "/ui/index.html"):
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/html")
    finally:
        client.close()
        server.memo_conn.close()


def pin_embeddings(monkeypatch, mapping):
    monkeypatch.setattr(embeddings, "embed",
                        lambda texts: [mapping[text] for text in texts])


def unit(*parts):
    vector = np.zeros(embeddings.DIM, dtype=np.float32)
    vector[:len(parts)] = parts
    vector /= np.linalg.norm(vector) + 1e-12
    return vector.astype(np.float32).tobytes()


def save_value(client, key, value):
    return save(client, key=key, type="context", value=value).json()


def test_graph_similar_shape_and_dedup(api, monkeypatch):
    client, _ = api
    pin_embeddings(monkeypatch, {"alpha": unit(1, 0),
                                  "beta": unit(0.9, 0.1),
                                  "gamma": unit(0.4, 0.6)})
    for key, value in (("m.a", "alpha"), ("m.b", "beta"), ("m.c", "gamma")):
        assert save_value(client, key, value)["action"] == "created"
    body = client.get("/api/graph", headers=AUTH).json()
    pairs = body["similar"]
    assert pairs
    keys = {node["key"] for node in body["nodes"]}
    for pair in pairs:
        assert set(pair) == {"src_key", "dst_key", "score"}
        assert pair["src_key"] != pair["dst_key"]
        assert pair["src_key"] in keys and pair["dst_key"] in keys
    unordered = {frozenset((p["src_key"], p["dst_key"])) for p in pairs}
    assert len(unordered) == len(pairs)
    scores = [pair["score"] for pair in pairs]
    assert scores == sorted(scores, reverse=True)
    assert all(score >= 0.35 for score in scores)


def test_graph_similar_retries_below_floor(api, monkeypatch):
    client, _ = api
    pin_embeddings(monkeypatch, {"alpha": unit(1, 0), "beta": unit(0.3, 1)})
    save_value(client, "m.a", "alpha")
    save_value(client, "m.b", "beta")
    pairs = client.get("/api/graph", headers=AUTH).json()["similar"]
    assert len(pairs) == 1
    assert 0.25 <= pairs[0]["score"] < 0.35


def test_graph_similar_empty_below_retry_floor(api, monkeypatch):
    client, _ = api
    pin_embeddings(monkeypatch, {"alpha": unit(1, 0), "beta": unit(0.2, 1)})
    save_value(client, "m.a", "alpha")
    save_value(client, "m.b", "beta")
    body = client.get("/api/graph", headers=AUTH).json()
    assert body["similar"] == []


def test_graph_similar_deterministic_across_calls(api, monkeypatch):
    client, _ = api
    pin_embeddings(monkeypatch, {"alpha": unit(1, 0),
                                  "beta": unit(0.9, 0.1),
                                  "gamma": unit(0.4, 0.6)})
    for key, value in (("m.a", "alpha"), ("m.b", "beta"), ("m.c", "gamma")):
        save_value(client, key, value)
    first = client.get("/api/graph", headers=AUTH).json()["similar"]
    second = client.get("/api/graph", headers=AUTH).json()["similar"]
    assert first == second
    assert first


def test_memory_related_capped_and_self_excluded(api, monkeypatch):
    client, _ = api
    same = unit(1, 0)
    pin_embeddings(monkeypatch, {f"value{index}": same for index in range(7)})
    for index in range(7):
        save_value(client, f"m.{index}", f"value{index}")
    related = client.get("/api/memories/m.0", headers=AUTH).json()["related"]
    assert len(related) == 5
    keys = [item["key"] for item in related]
    assert "m.0" not in keys
    assert len(set(keys)) == 5
    assert all(set(item) == {"key", "type", "status", "uses", "score"}
               for item in related)
    scores = [item["score"] for item in related]
    assert scores == sorted(scores, reverse=True)


def test_similar_and_related_without_embeddings(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(embeddings, "embed", lambda texts: [b"" for _ in texts])
    save_value(client, "m.a", "alpha")
    save_value(client, "m.b", "beta")
    assert client.get("/api/graph", headers=AUTH).json()["similar"] == []
    detail = client.get("/api/memories/m.a", headers=AUTH).json()
    assert detail["related"] == []


def test_ui_mount_reports_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(static_ui, "ui_dir", lambda: None)
    client, server = make_client(tmp_path)
    try:
        root = client.get("/ui", follow_redirects=False)
        assert root.status_code == 307, root.text
        assert root.headers["location"] == "/ui/"
        for path in ("/ui/", "/ui/index.html", "/ui/memories"):
            response = client.get(path)
            assert response.status_code == 404, response.text
            assert response.text == "dashboard not built - run 'make ui'"
    finally:
        client.close()
        server.memo_conn.close()
