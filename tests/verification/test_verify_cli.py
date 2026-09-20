"""Unit tests for the scruticell verify CLI (filter logic, statuses, matrix)."""

from __future__ import annotations

from scruticell.client import verify


def test_matrix_complete():
    assert verify.matrix_complete() == []


def test_manifest_urls():
    plan = {"step": {"step": "download", "manifest": [
        {"url": "http://a/x.gz", "name": "x.gz"},
        {"url": "", "name": "no-url"},
        {"url": "http://b/y.gz", "name": "y.gz"}]}}
    urls = verify._manifest_urls(plan)
    assert len(urls) == 2
    assert urls[0]["name"] == "x.gz"


def test_oversize_when_total_exceeds(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "_reachable", lambda host: True)

    def fake_plan_create(acc, src):
        return {"plan_id": "p1", "row_type": "sample", "metadata_only": False,
                "metadata_manifest": {},
                "step": {"step": "download", "manifest": [
                    {"url": "http://x/a", "name": "a"},
                    {"url": "http://x/b", "name": "b"}]}}

    monkeypatch.setattr("scruticell.client.api.plan_create", fake_plan_create)
    monkeypatch.setattr(verify, "_head_len", lambda url, timeout=20: 60_000_000)
    case = {"id": "c1", "omics": "bulk", "source": "geo_ncbi",
            "accession": "GSE1", "tier": "A", "host": "http://h/",
            "expect": {"row_type": "sample", "h5ad": True}}
    res = verify.run_one(case, tmp_path, max_mb=100)
    assert res["status"] == "OVERSIZE"
    assert "120.0MB" in res["detail"]


def test_blocked_when_host_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "_reachable", lambda host: False)
    case = {"id": "c1", "omics": "bulk", "source": "geo_ncbi",
            "accession": "GSE1", "tier": "A", "host": "http://h/", "expect": {}}
    res = verify.run_one(case, tmp_path)
    assert res["status"] == "BLOCKED"


def test_pass_metadata_only_case(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "_reachable", lambda host: True)

    def fake_plan_create(acc, src):
        return {"plan_id": "p1", "row_type": "sample", "metadata_only": True,
                "metadata_manifest": {src: {"role": "primary"}},
                "step": {"step": "download", "manifest": []}}

    monkeypatch.setattr("scruticell.client.api.plan_create", fake_plan_create)
    monkeypatch.setattr("scruticell.client.api.plan_step",
                        lambda pid, ok, result=None, error="": {"step": None,
                                                                "done": True})
    case = {"id": "portal", "omics": "门户", "source": "cellxgene",
            "accession": "cxg-x", "tier": "A", "host": "http://h/",
            "expect": {"row_type": "sample", "metadata_only": True}}
    res = verify.run_one(case, tmp_path)
    assert res["status"] == "PASS"


def test_run_matrix_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "_reachable", lambda host: True)
    monkeypatch.setattr(verify, "_head_len", lambda url, timeout=20: 1_000)
    from scruticell.client import api

    def fake_create(acc, src):
        return {"plan_id": "p", "row_type": "sample", "metadata_only": True,
                "metadata_manifest": {src: {"role": "primary"}},
                "step": {"step": "download", "manifest": []}}

    monkeypatch.setattr(api, "plan_create", fake_create)
    monkeypatch.setattr(api, "plan_step",
                        lambda pid, ok, result=None, error="": {"step": None,
                                                                "done": True})
    # run only the 3 smallest metadata-ish tier A cases
    summary = verify.run_matrix(tier="A", case_ids=["portal_cellxgene",
                                                    "ae_metadata"],
                                out_dir=tmp_path, concurrency=2)
    assert summary["summary"]["PASS"] == 2
    assert summary["coverage_missing"] == []
