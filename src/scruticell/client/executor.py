"""Local deterministic executor — runs one plan step at a time.

Source-agnostic: it only follows the plan's instructions (URL manifest,
file→sample map, row type + storage spec, alignment directives). No LLM, no
source knowledge, no embedded experience — everything data-specific is baked
into the plan by the server planner.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)


def execute_step(step: dict, workdir: Path) -> dict:
    kind = step.get("step", "")
    try:
        fn = _HANDLERS.get(kind)
        if fn is None:
            return {"ok": False, "error": f"未知步骤: {kind}"}
        return fn(step, workdir)
    except Exception as exc:  # noqa: BLE001
        logger.exception("step %s failed", kind)
        return {"ok": False, "error": f"{kind}: {str(exc)[:300]}"}


def _download(step: dict, workdir: Path) -> dict:
    import httpx

    manifest = step.get("manifest") or []
    got = 0
    errors = []
    for m in manifest:
        url = m.get("url") or ""
        dest = workdir / m.get("dest", "")
        if not url:
            errors.append(f"{m.get('name')}: 无 URL（{m.get('resolve_hint', '')}）")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
            got += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{m.get('name')}: {str(exc)[:160]}")
    return {"ok": len(errors) == 0, "result": {"downloaded": got,
                                               "total": len(manifest)},
            "error": "；".join(errors)[:400]}


def _metadata_download(step: dict, workdir: Path) -> dict:
    """Download cross-source metadata/related-deposit files (mirrors, repos)."""
    import httpx

    got, errors, total = 0, [], 0
    for s in step.get("sources", []) or []:
        for m in s.get("files", []) or []:
            total += 1
            url = m.get("url") or ""
            dest = workdir / m.get("dest", "")
            if not url:
                errors.append(f"{s.get('source')}/{m.get('name')}: 无 URL")
                continue
            if not url.startswith(("http://", "https://")):
                errors.append(f"{m.get('name')}: 不支持的协议 {url[:24]}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
                    r.raise_for_status()
                    with dest.open("wb") as f:
                        for chunk in r.iter_bytes(chunk_size=1 << 16):
                            f.write(chunk)
                got += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{m.get('name')}: {str(exc)[:160]}")
    return {"ok": len(errors) == 0, "result": {"downloaded": got, "total": total},
            "error": "；".join(errors)[:400]}


def _decompress(step: dict, workdir: Path) -> dict:
    out_dir = workdir / step.get("to", "download/decompressed")
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for f in step.get("files", []) or []:
        path = workdir / f
        if not path.exists():
            continue
        name = path.name
        target_name = name[:-3] if name.endswith(".gz") else name
        target = out_dir / target_name
        if name.endswith(".gz"):
            with gzip.open(path, "rb") as fi, target.open("wb") as fo:
                shutil.copyfileobj(fi, fo)
            done.append(target.name)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as tf:
                tf.extractall(out_dir, filter="data")
            done.append(target.name)
        else:
            done.append(name)
    return {"ok": True, "result": {"decompressed": done}}


def _map_samples(step: dict, workdir: Path) -> dict:
    samples = step.get("samples") or {}
    return {"ok": True, "result": {"samples": samples}}


def _classify(step: dict, workdir: Path) -> dict:
    return {"ok": True, "result": {"per_sample_modality":
                                   step.get("per_sample_modality", {})}}


def _effective_path(path: Path) -> Path:
    """Prefer the decompressed copy of a .gz file when present."""
    if path.name.endswith(".gz"):
        decomp = path.parent / "decompressed" / path.name[:-3]
        if decomp.exists():
            return decomp
    return path


def _from_reader(r: dict):
    import anndata as ad

    m = r.get("matrix")
    if m is None:
        return None
    adata = ad.AnnData(X=m)
    bcs = r.get("cell_barcodes")
    genes = r.get("gene_names")
    if bcs:
        adata.obs_names = bcs
    if genes:
        adata.var_names = genes
    return adata


def _build_anndata(path: Path) -> object | None:
    name = path.name.lower()
    try:
        if name.endswith(".h5ad"):
            import anndata as ad
            return ad.read_h5ad(path)
        if name.endswith((".h5", ".hdf5")):
            from scruticell.readers.h5ad import read_h5ad
            return _from_reader(read_h5ad(path))
        if name.endswith(".mtx"):
            from scruticell.readers.mtx import read_mtx
            return _from_reader(read_mtx(path.parent))
        if name.endswith((".csv", ".tsv", ".txt")):
            from scruticell.readers.csv_matrix import read_csv_matrix
            return _from_reader(read_csv_matrix(path))
        if name.endswith(".rds"):
            from scruticell.readers.rds import read_rds
            return _from_reader(read_rds(path))
        if name.endswith(".loom"):
            from scruticell.readers.loom import read_loom
            return _from_reader(read_loom(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("parse failed %s: %s", path.name, exc)
    return None


def _preserve_sidecars(datadir: Path, out: Path) -> list[str]:
    """Copy every file in a data dir (barcodes/features/images/positions/...) to
    sidecars/, so nothing the downstream analysis needs is lost."""
    sc = out / "sidecars"
    sc.mkdir(parents=True, exist_ok=True)
    kept = []
    if datadir.exists():
        for p in sorted(datadir.iterdir()):
            if p.is_file() and p.name != ".gitkeep":
                shutil.copy(p, sc / p.name)
                kept.append(str(sc / p.name))
    return kept


def _parse_matrix(step: dict, workdir: Path) -> dict:
    out = workdir / step.get("output", "preprocessed/group")
    out.mkdir(parents=True, exist_ok=True)
    sample = step.get("sample", "group")
    files = step.get("files", []) or []
    row_type = step.get("row_type", "sample")
    produced, notes, sidecars = [], [], []
    written = None
    for f in files:
        path = workdir / f
        p = _effective_path(path)
        if not path.exists() and not p.exists():
            notes.append(f"{f}: 缺失")
            continue
        target = p if p.exists() else path
        name = target.name
        low = name.lower()
        is_matrix = (low.endswith((".mtx", ".csv", ".tsv", ".txt", ".rds", ".loom",
                                   ".h5ad", ".h5", ".hdf5"))
                     and not ("barcode" in low or "feature" in low))
        if not is_matrix:
            # row-defining / image / QC files → preserved as sidecars
            sidecars.extend(_preserve_sidecars(target.parent, out))
            continue
        try:
            adata = _build_anndata(target)
        except Exception as exc:  # noqa: BLE001
            adata = None
            notes.append(f"{name}: 解析异常 {str(exc)[:120]}")
        if adata is None:
            shutil.copy(target, out / target.name)
            produced.append(target.name)
            continue
        sidecars.extend(_preserve_sidecars(target.parent, out))
        fname = f"{sample}.h5ad"
        adata.write(out / fname)
        written = out / fname
        produced.append(fname)
    result = {"sample": sample, "produced": produced, "notes": notes[:10],
              "sidecars": sidecars, "row_type": row_type}
    if written:
        result["ann_data"] = str(written)
    return {"ok": True, "result": result}


def _align_spatial(adata, out: Path) -> int:
    """Align tissue_positions barcodes to obs → obsm['spatial'] (px coords)."""
    import numpy as np

    sc = out / "sidecars"
    tps = None
    if sc.exists():
        for cand in sorted(sc.rglob("tissue_positions*.csv")):
            tps = cand
            break
    if tps is None:
        return 0
    import pandas as pd

    df = pd.read_csv(tps, header=None)
    rows = df.values
    if str(rows[0][0]).lower() in ("barcode", "key", "id"):
        rows = rows[1:]
    coords: dict = {}
    for r in rows:
        barcode = str(r[0])
        if not barcode or barcode.lower() in ("nan", "none"):
            continue
        try:
            py, px = float(r[-2]), float(r[-1])  # last two = (px_row, px_col)
        except (TypeError, ValueError):
            continue
        coords[barcode] = [px, py]  # obsm['spatial'] is x=col, y=row
    names = list(adata.obs_names)
    mat = np.full((len(names), 2), np.nan)
    n = 0
    for i, b in enumerate(names):
        if b in coords:
            mat[i] = coords[b]
            n += 1
    if n:
        adata.obsm["spatial"] = mat
        adata.uns["spatial"] = {"note": f"coords from {tps.name}",
                                "file": str(tps)}
    return n


def _align(step: dict, workdir: Path) -> dict:
    """Merge cross-source sample metadata onto the observation axis.

    row_type=sample: matrix columns are samples → match sample_id/gsm ids.
    row_type=barcode/spot: sample-level fields roll up as ``sample.*`` constant
    columns on every observation (cell/spot).
    """
    import anndata as ad

    sample = step.get("sample", "group")
    out = workdir / step.get("output", "preprocessed/group")
    row_type = step.get("row_type", "sample")
    h5ad = out / f"{sample}.h5ad"
    if not h5ad.exists():
        return {"ok": True, "result": {"sample": sample, "skipped": "no parsed h5ad"}}
    adata = ad.read_h5ad(h5ad)
    added: list[str] = []
    meta = list(step.get("sample_metadata") or [])
    related = step.get("related_metadata") or {}
    sources = [("primary", meta)] + [(k, v) for k, v in related.items()]
    for tag, rows in sources:
        if not rows:
            continue
        if row_type == "sample":
            n = 0
            for row in rows:
                sid = row.get("sample_id")
                if sid and sid in adata.obs_names:
                    for k, v in (row.get("characteristics") or {}).items():
                        adata.obs.loc[sid, k] = v
                    adata.obs.loc[sid, "sample_id"] = sid
                    adata.obs.loc[sid, "title"] = row.get("title", "")
                    n += 1
            if n:
                added.append(f"样本行匹配 {n}（{tag}）")
        else:
            # rollup sample-level metadata onto every cell/spot
            for row in rows:
                for k, v in (row.get("characteristics") or {}).items():
                    col = f"sample.{k}"
                    if col not in adata.obs.columns:
                        adata.obs[col] = v
                        added.append(col)
                col = "sample.sample_id"
                if col not in adata.obs.columns:
                    adata.obs[col] = row.get("sample_id", "")
                    added.append(col)
                break  # series-level constants
    if step.get("spatial") and row_type == "spot":
        n = _align_spatial(adata, out)
        if n:
            added.append(f"spatial 坐标 {n}")
    adata.write(h5ad)
    return {"ok": True, "result": {"sample": sample, "added": added,
                                   "obs": list(adata.obs.columns),
                                   "obsm": list(adata.obsm.keys()),
                                   "uns": list(adata.uns.keys())}}


def _assemble(step: dict, workdir: Path) -> dict:
    out = workdir / step.get("output", "preprocessed")
    out.parent.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "result": {"assembled": str(out)}}


def _report(step: dict, workdir: Path) -> dict:
    return {"ok": True, "result": {"output_dir": str(workdir)}}


_HANDLERS = {
    "download": _download,
    "metadata_download": _metadata_download,
    "decompress": _decompress,
    "map_samples": _map_samples,
    "classify": _classify,
    "parse_matrix": _parse_matrix,
    "align": _align,
    "assemble": _assemble,
    "report": _report,
}
