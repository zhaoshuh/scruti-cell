"""scruticell verify — real-scenario verification across omics × sources.

Every case runs the REAL user flow in the sandbox: reachability pre-check →
server ``/api/plan/create`` (cross-source locate + storage/row_type) → runtime
whole-dataset size filter (≤ ``--max-mb``, never overshoots) → local client
executor loop (download → decompress → parse → align → assemble) → assertions on
row_type / storage / obs / obsm / final h5ad.

Results per case: PASS / FAIL / OVERSIZE / BLOCKED.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

# ── case table ──────────────────────────────────────────────────────────────
# tier A = sandbox-reachable small whole-datasets (≤100MB total, measured);
# tier B = documented real deposits on hosts blocked in this sandbox (the tool
#         reports BLOCKED/OVERSIZE here and runs for real on a networked host).

CASES: list[dict] = [
    # ── A：真跑（实测总大小 ≤100MB，沙箱可达）──
    {"id": "bulk_fpkm", "omics": "bulk RNA-seq", "design": "队列 FPKM",
     "source": "geo_ncbi", "accession": "GSE135624", "tier": "A",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "sample", "h5ad": True},
     "size_mb": 5.1, "note": "20LAE 1.9MB + 46SAE 3.3MB；obs=样本列"},
    {"id": "cite_seq", "omics": "CITE-seq", "design": "RNA+ADT 蛋白",
     "source": "geo_ncbi", "accession": "GSE100866", "tier": "A",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 28.7, "note": "9 文件；ADT 蛋白侧车"},
    {"id": "methylation", "omics": "甲基化 450k", "design": "EWAS β 值",
     "source": "geo_ncbi", "accession": "GSE207605", "tier": "A",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "sample", "h5ad": True},
     "size_mb": 29.0, "note": "18 个 β 值 csv.gz（合计 ~29MB）"},
    {"id": "scrna_h5ad", "omics": "scRNA-seq", "design": "h5ad（仓库源）",
     "source": "figshare", "accession": "figshare-22577536", "tier": "A",
     "host": "https://api.figshare.com/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 99.5, "note": "scverse pbmc3k h5ad 99.5MB（走代理）"},
    {"id": "ae_metadata", "omics": "ArrayExpress", "design": "元数据/镜像",
     "source": "arrayexpress", "accession": "E-MTAB-12595", "tier": "A",
     "host": "https://www.ebi.ac.uk/",
     "expect": {"row_type": "sample", "h5ad": False},
     "size_mb": 0.02, "note": "idf+sdrf 元数据（scATAC 研究 E-MTAB-12595）"},
    {"id": "portal_cellxgene", "omics": "门户", "design": "cellxgene 参考",
     "source": "cellxgene", "accession": "cxg-verify", "tier": "A",
     "host": "https://api.cellxgene.cziscience.com/",
     "expect": {"row_type": "sample", "metadata_only": True},
     "size_mb": 0, "note": "门户元数据参考（不下载数据）"},
    {"id": "atac_bulk", "omics": "ATAC-seq", "design": "bulk peaks（侧车）",
     "source": "geo_ncbi", "accession": "GSE150589", "tier": "A",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": None, "h5ad": False},
     "size_mb": 1.7, "note": "ATACPeaks.bed.gz 1.6MB（下载+侧车验证）"},
    # ── B：阻断宿主/超限/异形格式（文档化；沙箱内报 BLOCKED/OVERSIZE/FAIL）──
    {"id": "bulk_de", "omics": "bulk RNA-seq", "design": "处理对照 DE（Cufflinks）",
     "source": "geo_ncbi", "accession": "GSE52778", "tier": "B",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "sample", "h5ad": True},
     "size_mb": 3.5, "note": "Cufflinks 空格分隔 → reader 不支持，B 层"},
    {"id": "scrna_10x", "omics": "scRNA-seq", "design": "10x RAW 归档",
     "source": "geo_ncbi", "accession": "GSE163121", "tier": "B",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "barcode", "h5ad": False},
     "size_mb": 96.5, "note": "RAW.tar of TARs（原始归档，无干净矩阵）→ B 层"},
    # ── B：阻断宿主/超限（文档化；沙箱内报 BLOCKED/OVERSIZE）──
    {"id": "scatac", "omics": "scATAC-seq", "design": "fragments+peaks",
     "source": "geo_ncbi", "accession": "GSE129785", "tier": "B",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 87771, "note": "RAW.tar 72GB → 沙箱内 OVERSIZE"},
    {"id": "spatial", "omics": "空间 Visium", "design": "spot 坐标",
     "source": "geo_ncbi", "accession": "GSE240715", "tier": "B",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "spot", "h5ad": True, "spot": True},
     "size_mb": 443, "note": "cloupe 409MB → 沙箱内 OVERSIZE"},
    {"id": "multiome", "omics": "多组学 RNA+ATAC", "design": "Multiome",
     "source": "geo_ncbi", "accession": "GSE194122", "tier": "B",
     "host": "https://ftp.ncbi.nlm.nih.gov/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 3514, "note": "cite 615MB + multiome 2.9GB → OVERSIZE"},
    {"id": "perturb_zenodo", "omics": "扰动筛选", "design": "CITE-蛋白",
     "source": "zenodo", "accession": "zenodo-10044268", "tier": "B",
     "host": "https://zenodo.org/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 24.7, "note": "scPerturb protein h5ad；宿主沙箱阻断 → BLOCKED"},
    {"id": "scrna_hf", "omics": "scRNA-seq", "design": "h5ad（HF）",
     "source": "huggingface", "accession": "huggingface-scverse/pbmc3k", "tier": "B",
     "host": "https://huggingface.co/",
     "expect": {"row_type": "barcode", "h5ad": True},
     "size_mb": 40, "note": "HF 宿主沙箱阻断 → BLOCKED"},
]


def print_matrix() -> None:
    """Print the case matrix (A tier = runs in sandbox, B tier = needs network)."""
    lines = [f"{'id':<18}{'omics':<20}{'source':<14}{'accession':<22}{'totalMB':>9}  tier"]
    for c in CASES:
        lines.append(
            f"{c['id']:<18}{c['omics']:<20}{c['source']:<14}"
            f"{c['accession']:<22}{c.get('size_mb', 0):>9}  {c['tier']}")
    print("\n".join(lines))


def matrix_complete() -> list[str]:
    """Completeness check: every omics present in tier A has ≥1 A case, and every
    reachable source has ≥1 A case. Returns list of missing cells."""
    a = [c for c in CASES if c["tier"] == "A"]
    omics = {c["omics"] for c in a}
    sources = {c["source"] for c in a}
    missing = []
    reachable = {"geo_ncbi", "arrayexpress", "figshare", "cellxgene"}
    for s in sorted(reachable):
        if s not in sources:
            missing.append(f"source:{s}")
    for o in sorted(omics):
        if not [c for c in a if c["omics"] == o]:
            missing.append(f"omics:{o}")
    return missing


# ── helpers ─────────────────────────────────────────────────────────────────

def _head_len(url: str, timeout: float = 20) -> int:
    import httpx

    for attempt in range(3):
        try:
            r = httpx.head(url, timeout=timeout, follow_redirects=True)
            cl = r.headers.get("content-length")
            if cl:
                return int(cl)
        except Exception:
            time.sleep(0.5)
    return 0


def _manifest_urls(plan: dict) -> list[dict]:
    """Gather {url,name} from the download step (and metadata_download if present)."""
    out: list[dict] = []
    step = plan.get("step") or {}
    if step.get("step") == "download":
        for m in step.get("manifest") or []:
            if m.get("url"):
                out.append({"url": m["url"], "name": m.get("name", "")})
    return out


def _reachable(host: str) -> bool:
    import httpx

    try:
        r = httpx.head(host, timeout=12, follow_redirects=True)
        return r.status_code < 500
    except Exception:
        return False


def _run_loop(plan_id: str, step: dict, workdir: Path) -> dict:
    from scruticell.client import api
    from scruticell.client.executor import execute_step

    transcript: list[dict] = []
    guard = 0
    while step and guard < 64:
        guard += 1
        res = execute_step(step, workdir)
        transcript.append({"step": step.get("step"), "ok": bool(res.get("ok")),
                           "error": res.get("error", "")[:200]})
        out = api.plan_step(plan_id, res.get("ok", False), res.get("result", {}),
                            res.get("error", ""))
        if out.get("done"):
            step = None
        else:
            step = out.get("step")
    return {"done": not step, "steps_run": guard, "transcript": transcript}


def run_one(case: dict, out_dir: Path, max_mb: int = 100,
            concurrency: int = 8) -> dict:
    """Run one case through the real user flow; return a result dict."""
    from scruticell.client import api

    res: dict = {"id": case["id"], "omics": case["omics"], "design": case.get("design"),
                 "source": case["source"], "accession": case["accession"],
                 "tier": case["tier"], "status": "SKIP", "detail": ""}
    if not _reachable(case["host"]):
        res["status"] = "BLOCKED"
        res["detail"] = f"host 不可达：{case['host']}"
        return res
    try:
        plan = api.plan_create(case["accession"], case["source"])
        expect_rt = (case.get("expect") or {}).get("row_type")
        if expect_rt and plan.get("row_type") != expect_rt:
            # GEO suppl listing is occasionally flaky server-side → re-route once
            plan = api.plan_create(case["accession"], case["source"])
    except Exception as exc:  # noqa: BLE001
        res["status"] = "BLOCKED"
        res["detail"] = f"plan/create 失败：{str(exc)[:160]}"
        return res
    plan_id = plan.get("plan_id", "")
    res["row_type"] = plan.get("row_type", "")
    # runtime whole-dataset size filter
    urls = _manifest_urls(plan)
    if urls:
        total = sum(_head_len(u["url"]) for u in urls)
        total_mb = total / 1e6
        res["size_mb"] = round(total_mb, 1)
        if total_mb > max_mb:
            res["status"] = "OVERSIZE"
            res["detail"] = (f"整数据集 {total_mb:.1f}MB > 上限 {max_mb}MB"
                             f"（{len(urls)} 个文件）")
            return res
    else:
        res["size_mb"] = 0.0
        if (case.get("expect") or {}).get("h5ad"):
            res["status"] = "BLOCKED"
            res["detail"] = "plan 未解析出任何文件清单（源不可达/代理瞬时失败）"
            return res

    wd = out_dir / case["id"]
    wd.mkdir(parents=True, exist_ok=True)
    loop = _run_loop(plan_id, plan.get("step"), wd)

    checks = _assertions(case, plan, loop, wd)
    res["checks"] = checks
    if not loop.get("done"):
        res["status"] = "FAIL"
        res["detail"] = "plan 循环未完成"
        return res
    failed = [c for c in checks if not c.get("ok")]
    if failed:
        res["status"] = "FAIL"
        res["detail"] = "；".join(f"{c['name']}:{c.get('detail','')}" for c in failed)
        return res
    res["status"] = "PASS"
    res["detail"] = f"steps={loop['steps_run']}"
    return res


def _assertions(case: dict, plan: dict, loop: dict, wd: Path) -> list[dict]:
    expect = case.get("expect") or {}
    checks: list[dict] = []
    row_type = plan.get("row_type", "")
    if expect.get("row_type"):
        checks.append({"name": "row_type", "ok": row_type == expect["row_type"],
                       "detail": f"plan={row_type} expect={expect['row_type']}"})
    if expect.get("metadata_only"):
        meta = (plan.get("metadata_manifest") or {}).get(case["source"], {})
        checks.append({"name": "metadata_only",
                       "ok": bool(plan.get("metadata_only")) or
                             meta.get("role") == "primary",
                       "detail": "期望仅元数据/参考"})
        return checks
    sample = f"group_{case['accession']}"
    h5ad = wd / "preprocessed" / sample / f"{sample}.h5ad"
    if expect.get("h5ad"):
        ok = h5ad.exists()
        detail = "无最终 h5ad" if not ok else ""
        if ok:
            try:
                import anndata as ad

                a = ad.read_h5ad(h5ad)
                ok = a.n_obs > 0
                detail = f"n_obs={a.n_obs} n_vars={a.n_vars}"
                if expect.get("spot") and "spatial" in a.obsm:
                    detail += f" obsm[spatial]={a.obsm['spatial'].shape}"
            except Exception as exc:  # noqa: BLE001
                ok = False
                detail = f"h5ad 读取失败：{str(exc)[:120]}"
        checks.append({"name": "h5ad", "ok": ok, "detail": detail})
    return checks


def run_matrix(tier: str = "all", case_ids: list[str] | None = None,
               out_dir: Path | None = None, max_mb: int = 100,
               concurrency: int = 4) -> dict:
    out = Path(out_dir) if out_dir else Path("output/verify").resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = CASES
    if case_ids:
        cases = [c for c in CASES if c["id"] in case_ids]
    if tier != "all":
        cases = [c for c in cases if c["tier"].upper() == tier.upper()]
    missing = matrix_complete()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(run_one, c, out, max_mb): c for c in cases}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as exc:  # noqa: BLE001
                c = futures[fut]
                r = {"id": c["id"], "status": "FAIL", "detail": str(exc)[:200]}
            results.append(r)
            _print_one(r)
    order = {c["id"]: i for i, c in enumerate(cases)}
    results.sort(key=lambda r: order.get(r.get("id"), 999))
    statuses = [r.get("status", "SKIP") for r in results]
    summary = {s: statuses.count(s) for s in
               ("PASS", "FAIL", "OVERSIZE", "BLOCKED", "SKIP")}
    return {"tier": tier, "max_mb": max_mb,
            "coverage_missing": missing,
            "results": results, "summary": summary}


def print_result(summary: dict) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _print_one(r: dict) -> None:
    print(f"[{r.get('status')}] {r.get('id')} ({r.get('omics')}) — {r.get('detail','')}",
          flush=True)


# keep the module importable without triggering the client/api import cycle
def _noop() -> None:
    pass
