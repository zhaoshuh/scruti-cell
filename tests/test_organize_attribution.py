"""Tests for LLM-driven file attribution in organize_output."""

import json

import pytest

import scruticell.config
import scruticell.preprocessing.attribution
from scruticell.preprocessing.tools.organize_tool import organize_output

SAMPLE_GROUPS = [
    {
        "gsm_ids": ["GSM1"],
        "sample_key": "GSM1",
        "key_source": "gsm_id",
        "file_prefix": "",
        "modalities": {
            "scrna_seq": {
                "modality": "scrna_seq",
                "has_demux": False,
            }
        },
    }
]


def _make_layout(tmp_path):
    dl = tmp_path / "download"
    (dl / "GSM1").mkdir(parents=True)
    (dl / "GSM1" / "barcodes.tsv.gz").write_text("barcode\nA\n")
    (dl / "GSM1" / "matrix.mtx").write_text("1 1 1\n")
    (dl / "README.pdf").write_text("pdf")
    out = tmp_path / "out"
    return str(dl), str(out)


def test_attribution_links_files(tmp_path, monkeypatch):
    dl, out = _make_layout(tmp_path)
    monkeypatch.setattr(
        scruticell.preprocessing.attribution,
        "llm_attribution",
        lambda download_root, sample_groups, gse_id: {
            "ok": True,
            "files": [
                {"ancestry": "GSM1/barcodes.tsv.gz", "sample_key": "GSM1",
                 "modality": "scrna_seq", "role": "barcodes"},
                {"ancestry": "GSM1/matrix.mtx", "sample_key": "GSM1",
                 "modality": "scrna_seq", "role": "matrix"},
            ],
            "shared": [],
            "unclassified": ["README.pdf"],
        },
    )
    classification = {"sample_groups": SAMPLE_GROUPS,
                      "unclassified": [], "shared_file_groups": []}
    r = organize_output("GSE1", classification, dl, output_dir=out)
    assert r["errors"] == []
    assert r["total_files_linked"] == 2
    mod_dir = tmp_path / "out" / "preprocessed" / "GSM1" / "scrna_seq"
    assert (mod_dir / "barcodes.tsv.gz").exists()
    assert (mod_dir / "matrix.mtx").exists()
    # unclassified recorded from attribution
    uncl = json.loads((tmp_path / "out" / "preprocessed" / "unclassified"
                       / "unclassified.json").read_text())
    assert "README.pdf" in uncl


def test_attribution_failure_routes_to_unclassified(tmp_path, monkeypatch):
    dl, out = _make_layout(tmp_path)
    monkeypatch.setattr(
        scruticell.preprocessing.attribution,
        "llm_attribution",
        lambda *a, **k: {"ok": False, "files": [], "shared": [],
                         "unclassified": [], "error": "no key"},
    )
    classification = {"sample_groups": SAMPLE_GROUPS,
                      "unclassified": [], "shared_file_groups": []}
    r = organize_output("GSE1", classification, dl, output_dir=out)
    assert r["total_files_linked"] == 0
    assert not (tmp_path / "out" / "preprocessed" / "GSM1" / "scrna_seq"
                / "matrix.mtx").exists()


def test_attribution_disabled_uses_provided_files(tmp_path, monkeypatch):
    dl, out = _make_layout(tmp_path)
    monkeypatch.setattr(scruticell.config, "LLM_ATTRIBUTION_ENABLED", False)
    classification = {
        "sample_groups": [{
            "gsm_ids": ["GSM1"], "sample_key": "GSM1", "key_source": "gsm_id",
            "file_prefix": "",
            "modalities": {"scrna_seq": {"modality": "scrna_seq", "has_demux": False,
                                         "files": [
                                             {"source_path": "GSM1/matrix.mtx",
                                              "ancestry": "GSM1/matrix.mtx",
                                              "role": "matrix"}]}},
        }],
        "unclassified": [], "shared_file_groups": [],
    }
    r = organize_output("GSE1", classification, dl, output_dir=out)
    assert r["total_files_linked"] == 1
    assert (tmp_path / "out" / "preprocessed" / "GSM1" / "scrna_seq"
            / "matrix.mtx").exists()