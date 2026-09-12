"""Local deterministic executor — runs one plan step at a time.

Source-agnostic: it only follows the plan's instructions (URL manifest,
file→sample map, modality tags, parse directives). No LLM, no source knowledge.
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


def _parse_matrix(step: dict, workdir: Path) -> dict:
    out = workdir / step.get("output", "preprocessed/group")
    out.mkdir(parents=True, exist_ok=True)
    sample = step.get("sample", "group")
    files = step.get("files", []) or []
    produced = []
    notes = []
    for f in files:
        path = workdir / f
        if not path.exists():
            notes.append(f"{f}: 缺失")
            continue
        name = path.name.lower()
        try:
            if name.endswith((".h5ad", ".h5")):
                shutil.copy(path, out / path.name)
                produced.append(path.name)
            elif name.endswith(".mtx") or "matrix.mtx" in name:
                import scipy.io as _sio
                from scipy.sparse import csr_matrix

                mtx = _sio.mmread(path)
                if hasattr(mtx, "tocsr"):
                    mtx = mtx.tocsr()
                try:
                    import anndata as _ad

                    adata = _ad.AnnData(X=csr_matrix(mtx))
                    outfile = out / f"{sample}.h5ad"
                    adata.write(outfile)
                    produced.append(outfile.name)
                except Exception:  # noqa: BLE001
                    notes.append(f"{name}: 转 AnnData 失败（保留矩阵）")
                    shutil.copy(path, out / path.name)
            else:
                notes.append(f"{name}: 未知矩阵格式，保留原文件")
                shutil.copy(path, out / path.name)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}: {str(exc)[:120]}")
    return {"ok": True, "result": {"sample": sample, "produced": produced,
                                   "notes": notes[:10]}}


def _assemble(step: dict, workdir: Path) -> dict:
    out = workdir / step.get("output", "preprocessed")
    out.parent.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "result": {"assembled": str(out)}}


def _report(step: dict, workdir: Path) -> dict:
    return {"ok": True, "result": {"output_dir": str(workdir)}}


_HANDLERS = {
    "download": _download,
    "decompress": _decompress,
    "map_samples": _map_samples,
    "classify": _classify,
    "parse_matrix": _parse_matrix,
    "assemble": _assemble,
    "report": _report,
}
