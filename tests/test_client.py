"""Tests for the coding-agent client (executor + api + loop)."""


def test_executor_steps(tmp_path):
    from scruticell.client.executor import execute_step

    # download needs network — test the deterministic ones
    assert execute_step({"step": "map_samples", "samples": {"g": ["a"]}}, tmp_path)[
        "ok"] is True
    assert execute_step({"step": "classify", "per_sample_modality": {"g": ["scrna_seq"]}},
                        tmp_path)["ok"] is True
    assert execute_step({"step": "report"}, tmp_path)["ok"] is True
    assert execute_step({"step": "unknown"}, tmp_path)["ok"] is False


def test_executor_decompress(tmp_path):
    import gzip

    from scruticell.client.executor import execute_step

    (tmp_path / "a.txt.gz").parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(tmp_path / "a.txt.gz", "wt") as f:
        f.write("hello")
    res = execute_step({"step": "decompress",
                        "files": ["a.txt.gz"], "to": "out"}, tmp_path)
    assert res["ok"] is True
    assert (tmp_path / "out" / "a.txt").exists()


def test_api_uses_key_and_url(monkeypatch):
    import httpx

    from scruticell.client import api

    monkeypatch.setenv("SCRUTICELL_SERVER_URL", "http://srv")
    monkeypatch.setenv("SCRUTICELL_API_KEY", "sk-test")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"task": "vcc2026", "recommendations": []}

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=True):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = api.search_datasets("x")
    assert seen["url"] == "http://srv/api/search"
    assert seen["auth"] == "Bearer sk-test"
    assert out["task"] == "vcc2026"


def test_plan_run_loop(monkeypatch, tmp_path):
    import httpx

    from scruticell.client import run

    steps = [
        {"step": "download", "manifest": []},
        {"step": "decompress", "files": []},
        {"step": "map_samples", "samples": {"g": []}},
        {"step": "classify", "per_sample_modality": {"g": ["scrna_seq"]}},
        {"step": "parse_matrix", "files": [], "sample": "g", "output": "o"},
        {"step": "assemble", "output": "a.h5ad"},
        {"step": "report"},
    ]

    state = {"cursor": 0}

    def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=True):
        if url.endswith("/api/plan/create"):
            return type("R", (), {"raise_for_status": lambda self: None,
                                  "json": lambda self: {
                                      "plan_id": "p1", "step": steps[0],
                                      "steps_total": len(steps)}})()
        if url.endswith("/api/plan/step"):
            i = state["cursor"]
            done = i + 1 >= len(steps)
            state["cursor"] += 1
            return type("R", (), {"raise_for_status": lambda self: None,
                                  "json": lambda self: {
                                      "step": None if done else steps[i + 1],
                                      "done": done}})()
        return type("R", (), {"raise_for_status": lambda self: None,
                              "json": lambda self: {}})()

    monkeypatch.setattr(httpx, "post", fake_post)
    res = run.plan_run("GSE1", workdir=str(tmp_path))
    assert res["done"] is True
    assert res["plan_id"] == "p1"
    assert res["steps_run"] == len(steps)
