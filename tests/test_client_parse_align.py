"""Tests for executor parse_matrix / align / metadata_download (row-type aware)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _write_mtx_dir(base):
    import scipy.io as sio
    from scipy.sparse import csr_matrix

    mtx_dir = base / "download" / "GSE1"
    mtx_dir.mkdir(parents=True, exist_ok=True)
    (mtx_dir / "barcodes.tsv").write_text("AAAC\nTTTG\nGGCC\n")
    (mtx_dir / "features.tsv").write_text("GENE1\nGENE2\n")
    sio.mmwrite(mtx_dir / "matrix.mtx",
                csr_matrix([[1, 0, 2], [0, 3, 1]]))
    return mtx_dir


def test_parse_matrix_barcode(tmp_path):
    from scruticell.client.executor import execute_step

    _write_mtx_dir(tmp_path)
    res = execute_step(
        {"step": "parse_matrix", "sample": "group_GSE1",
         "files": ["download/GSE1/matrix.mtx", "download/GSE1/barcodes.tsv",
                   "download/GSE1/features.tsv"],
         "row_type": "barcode", "output": "preprocessed/group_GSE1"},
        tmp_path)
    assert res["ok"] is True
    import anndata as ad
    adata = ad.read_h5ad(tmp_path / "preprocessed" / "group_GSE1" /
                         "group_GSE1.h5ad")
    assert adata.shape == (3, 2)
    assert list(adata.obs_names) == ["AAAC", "TTTG", "GGCC"]
    assert list(adata.var_names) == ["GENE1", "GENE2"]
    # barcodes/features preserved as sidecars
    sc = tmp_path / "preprocessed" / "group_GSE1" / "sidecars"
    assert (sc / "barcodes.tsv").exists()


def test_parse_matrix_bulk_csv(tmp_path):
    from scruticell.client.executor import execute_step

    d = tmp_path / "download" / "GSE2"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"GSM1": [1.0, 2.0], "GSM2": [3.0, 4.0]},
        index=["GENE1", "GENE2"]).to_csv(d / "counts.csv")
    res = execute_step(
        {"step": "parse_matrix", "sample": "group_GSE2",
         "files": ["download/GSE2/counts.csv"], "row_type": "sample",
         "output": "preprocessed/group_GSE2"}, tmp_path)
    assert res["ok"] is True
    import anndata as ad
    adata = ad.read_h5ad(tmp_path / "preprocessed" / "group_GSE2" /
                         "group_GSE2.h5ad")
    assert list(adata.obs_names) == ["GSM1", "GSM2"]


def test_align_barcode_rollup(tmp_path):
    from scruticell.client.executor import execute_step

    _write_mtx_dir(tmp_path)
    parse = {"step": "parse_matrix", "sample": "group_GSE1",
             "files": ["download/GSE1/matrix.mtx"],
             "row_type": "barcode", "output": "preprocessed/group_GSE1"}
    assert execute_step(parse, tmp_path)["ok"] is True
    res = execute_step(
        {"step": "align", "sample": "group_GSE1", "row_type": "barcode",
         "source": "geo_ncbi", "rollup": True,
         "sample_metadata": [
             {"sample_id": "GSM1", "title": "s1",
              "characteristics": {"tissue": "liver", "disease": "healthy"}}],
         "related_metadata": {}, "spatial": {},
         "output": "preprocessed/group_GSE1"}, tmp_path)
    assert res["ok"] is True
    import anndata as ad
    adata = ad.read_h5ad(tmp_path / "preprocessed" / "group_GSE1" /
                         "group_GSE1.h5ad")
    assert adata.obs["sample.tissue"].iloc[0] == "liver"
    assert adata.obs["sample.sample_id"].iloc[0] == "GSM1"


def test_align_bulk_sample_rows(tmp_path):
    from scruticell.client.executor import execute_step

    d = tmp_path / "download" / "GSE3"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"GSM10": [1.0], "GSM20": [2.0]}, index=["GENE1"]).to_csv(d / "m.csv")
    parse = {"step": "parse_matrix", "sample": "group_GSE3",
             "files": ["download/GSE3/m.csv"], "row_type": "sample",
             "output": "preprocessed/group_GSE3"}
    assert execute_step(parse, tmp_path)["ok"] is True
    res = execute_step(
        {"step": "align", "sample": "group_GSE3", "row_type": "sample",
         "source": "geo_ncbi", "rollup": False,
         "sample_metadata": [
             {"sample_id": "GSM10", "title": "t10",
              "characteristics": {"condition": "ctl"}},
             {"sample_id": "GSM20", "title": "t20",
              "characteristics": {"condition": "trt"}}],
         "related_metadata": {}, "spatial": {},
         "output": "preprocessed/group_GSE3"}, tmp_path)
    assert res["ok"] is True
    import anndata as ad
    adata = ad.read_h5ad(tmp_path / "preprocessed" / "group_GSE3" /
                         "group_GSE3.h5ad")
    assert adata.obs.loc["GSM10", "condition"] == "ctl"
    assert adata.obs.loc["GSM20", "condition"] == "trt"


def test_align_spatial(tmp_path):
    from scruticell.client.executor import execute_step

    _write_mtx_dir(tmp_path)
    parse = {"step": "parse_matrix", "sample": "group_GSE1",
             "files": ["download/GSE1/matrix.mtx"],
             "row_type": "spot", "output": "preprocessed/group_GSE1"}
    assert execute_step(parse, tmp_path)["ok"] is True
    sc = tmp_path / "preprocessed" / "group_GSE1" / "sidecars"
    sc.mkdir(parents=True, exist_ok=True)
    (sc / "tissue_positions.csv").write_text(
        "barcode,in_tissue,array_row,array_col,pxl_row_in_fullres,pxl_col_in_fullres\n"
        "AAAC,1,0,0,100,200\nTTTG,1,0,1,300,400\nGGCC,1,1,0,500,600\n")
    res = execute_step(
        {"step": "align", "sample": "group_GSE1", "row_type": "spot",
         "source": "geo_ncbi", "rollup": True, "sample_metadata": [],
         "related_metadata": {}, "spatial": {"obsm": "spatial", "uns": "spatial"},
         "output": "preprocessed/group_GSE1"}, tmp_path)
    assert res["ok"] is True
    import anndata as ad
    adata = ad.read_h5ad(tmp_path / "preprocessed" / "group_GSE1" /
                         "group_GSE1.h5ad")
    assert "spatial" in adata.obsm
    np.testing.assert_allclose(adata.obsm["spatial"][0], [200, 100])
    assert "spatial" in adata.uns


def test_metadata_download(tmp_path, monkeypatch):
    import httpx

    from scruticell.client.executor import execute_step

    class FakeResp:
        def __init__(self):
            self._chunks = [b"abc", b"def"]

        def raise_for_status(self):
            pass

        def iter_bytes(self, chunk_size=1 << 16):
            return iter(self._chunks)

    class FakeCtx:
        def __enter__(self):
            return FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "stream",
                        lambda method, url, **kw: FakeCtx())
    res = execute_step(
        {"step": "metadata_download",
         "sources": [{"source": "arrayexpress", "role": "mirror",
                      "files": [{"url": "http://x/f.txt",
                                 "name": "f.txt",
                                 "dest": "metadata/GSE1/arrayexpress/f.txt"}]}]},
        tmp_path)
    assert res["ok"] is True
    assert (tmp_path / "metadata" / "GSE1" / "arrayexpress" / "f.txt").read_bytes() \
        == b"abcdef"
