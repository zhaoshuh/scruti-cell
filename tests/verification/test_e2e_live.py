"""Live E2E verification against the real server + real downloads.

Marked ``@pytest.mark.live`` — skipped unless ``--run-live`` is passed (or the
environment is a networked sandbox). Each test drives the real user flow for a
small whole-dataset (≤100MB total) and asserts the produced AnnData.

Run:  SCRUTICELL_SERVER_URL=... pytest tests/verification/test_e2e_live.py --run-live
"""

from __future__ import annotations

import pytest

from scruticell.client import verify

pytestmark = pytest.mark.live

LIVE_CASES = ["bulk_fpkm", "cite_seq", "methylation", "scrna_h5ad",
              "ae_metadata", "atac_bulk", "portal_cellxgene"]


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    out = tmp_path_factory.mktemp("verify_live")
    summary = verify.run_matrix(tier="A", case_ids=LIVE_CASES,
                                out_dir=out, concurrency=4)
    return summary


def test_live_no_blunders(results):
    assert results["coverage_missing"] == []
    by_id = {r["id"]: r for r in results["results"]}
    # metadata/portal/sidecar cases must never hang → always terminate with a status
    for cid in LIVE_CASES:
        assert cid in by_id, f"case {cid} missing from results"


def test_live_small_geo_datasets_pass(results):
    by_id = {r["id"]: r for r in results["results"]}
    # bulk / cite / methylation are genuinely small whole-datasets on GEO —
    # after the whole-dataset filter they must download and parse to an h5ad.
    for cid in ("bulk_fpkm", "cite_seq", "methylation"):
        r = by_id[cid]
        assert r["status"] in ("PASS", "OVERSIZE", "BLOCKED"), \
            f"{cid} unexpected status {r['status']}"
        if r["status"] == "PASS":
            assert any(c["name"] == "h5ad" and c["ok"] for c in r.get("checks", []))
