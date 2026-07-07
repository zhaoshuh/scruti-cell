#!/usr/bin/env python
"""
Scrut-Cell Batch Benchmark Runner
==================================
一脚本完成：清空 KB → 逐个测试 GSE → 记录日志 → 汇总报告

GSE 列表来源：test_gse.xlsx 的 gse_id 列（自动读取）

Usage:
    python tests/benchmark/run_benchmark.py              # 跑全部
    python tests/benchmark/run_benchmark.py --failed     # 仅重测上次失败的
    python tests/benchmark/run_benchmark.py --resume     # 断点续跑
    python tests/benchmark/run_benchmark.py --keep-data  # 保留下载数据
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ---- 配置 ----

PROJECT_ROOT = Path(__file__).parent.parent.parent

# Excel 中第 7 行的注释：运行前提醒删除同一目录下的下载文件
# 脚本已内置 kb-clear 步骤，无需额外处理


def load_datasets() -> list[dict[str, Any]]:
    """从 test_gse.xlsx 读取 GSE 列表，返回统一格式的 DATASETS。

    按列名 "gse_id" 查找（不依赖列位置）。
    如果 Excel 不存在或读取失败，回退到内置默认列表。
    """
    xlsx_path = PROJECT_ROOT / "test_gse.xlsx"
    if not xlsx_path.exists():
        print(f"[WARN] {xlsx_path} not found, using built-in defaults")
        return _default_datasets()

    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
        ws = wb[wb.sheetnames[0]]
        # Find column index by header name "gse_id"
        headers = [str(c.value or "").strip().lower() for c in next(ws.iter_rows(max_row=1))]
        gse_col = next((i for i, h in enumerate(headers) if h == "gse_id"), 0)
        note_col = gse_col + 1 if gse_col + 1 < len(headers) else -1
        datasets = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            gse = str(row[gse_col]).strip() if gse_col < len(row) and row[gse_col] else ""
            if not gse.upper().startswith("GSE"):
                continue
            note = str(row[note_col]).strip() if note_col >= 0 and note_col < len(row) and row[note_col] else ""
            datasets.append({
                "id": f"T{i - 2:02d}",
                "gse_id": gse,
                "label": note or gse,
                "category": "批量测试",
                "features": "",
            })
        wb.close()
        if datasets:
            datasets = datasets[:20]
            print(f"[OK] Loaded {len(datasets)} GSE IDs from {xlsx_path.name}")
            return datasets
    except Exception as exc:
        print(f"[WARN] Failed to read {xlsx_path}: {exc}")

    return _default_datasets()


def load_failed_from_last_run() -> list[dict[str, Any]]:
    """从上一次运行结果中读取所有失败的 GSE。

    读取 results/latest/summary.json 或最新的结果目录，
    提取失败项的 gse_id，构造与 load_datasets() 一致的格式。
    """
    results_dir = PROJECT_ROOT / "tests" / "benchmark" / "results"

    # 优先用 latest 软链接
    latest = results_dir / "latest"
    if latest.is_symlink() or (latest.exists() and latest.is_dir()):
        summary_path = latest / "summary.json"
    else:
        # 回退：找最新的时间戳目录
        run_dirs = sorted(
            [d for d in results_dir.iterdir() if d.is_dir() and d.name != "latest"],
            reverse=True,
        )
        if not run_dirs:
            print("[WARN] No previous results found")
            return []
        summary_path = run_dirs[0] / "summary.json"

    if not summary_path.exists():
        print(f"[WARN] {summary_path} not found")
        return []

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        failures = summary.get("failures", [])
        if not failures:
            print("[INFO] No failures in last run")
            return []

        datasets = []
        for i, f in enumerate(failures):
            if f["gse_id"]!='GSE303943':
                datasets.append({
                    "id": f.get("id", f"R{i:02d}"),
                    "gse_id": f["gse_id"],
                    "label": f.get("gse_id", ""),
                    "category": "重测失败",
                    "features": "",
                })
        print(f"[OK] Loaded {len(datasets)} failed GSE IDs from previous run ({summary_path})")
        return datasets
    except Exception as exc:
        print(f"[WARN] Failed to read {summary_path}: {exc}")
        return []


def _default_datasets() -> list[dict[str, Any]]:
    """内置回退数据集列表。（仅当 Excel 不可用时使用）"""
    return [
        {"id": "T01", "gse_id": "GSE334035", "label": "3 GSM h5, 已验证",
         "category": "基础集", "features": ""},
        {"id": "T02", "gse_id": "GSE243292", "label": "已验证",
         "category": "基础集", "features": ""},
        {"id": "T03", "gse_id": "GSE285606", "label": "已验证",
         "category": "基础集", "features": ""},
    ]

# 运行配置
TIMEOUT_PER_GSE: int | None = None  # 无限制
SCRUTICELL_BIN = "scruticell"
RESULTS_DIR = PROJECT_ROOT / "tests" / "benchmark" / "results"


# ---- 工具函数 ----

def run_cmd(cmd: list[str], timeout: int | None = None) -> tuple[int, str, str]:
    """运行命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT), encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except Exception as e:
        return -2, "", str(e)


def clear_kb() -> str:
    """清空知识库中的策略数据。返回输出文本。"""
    rc, out, err = run_cmd([SCRUTICELL_BIN, "db-clear", "--yes"], timeout=30)
    return (out + err).strip()


def _cleanup_gse_output(gse_id: str) -> None:
    """Remove download, extracted files, and .h5ad for a GSE to free disk space.
    Keeps report files (JSON, log, CSV, readers/).
    """
    import shutil
    gse_dir = PROJECT_ROOT / "output" / gse_id
    if not gse_dir.exists():
        return
    try:
        # Remove download directory (largest)
        download_dir = gse_dir / "download"
        if download_dir.exists():
            shutil.rmtree(download_dir)
        # Remove .h5ad files
        for h5 in gse_dir.rglob("*.h5ad"):
            h5.unlink()
    except Exception as exc:
        print(f"  [WARN] Cleanup failed for {gse_id}: {exc}")


def test_one_gse(gse_id: str, keep_data: bool = False) -> dict[str, Any]:
    """测试单个 GSE，返回结果字典。"""
    result: dict[str, Any] = {
        "gse_id": gse_id,
        "success": False,
        "returncode": -1,
        "started_at": datetime.now().isoformat(),
        "stages": {},
        "metrics": {},
        "errors": [],
    }

    t0 = time.time()
    cmd = [SCRUTICELL_BIN, "run", gse_id]
    if keep_data:
        cmd.append("--keep-data")

    rc, stdout, stderr = run_cmd(cmd, timeout=TIMEOUT_PER_GSE)
    result["returncode"] = rc
    result["duration_s"] = round(time.time() - t0, 1)
    result["stdout"] = stdout
    result["stderr"] = stderr

    # 解析输出，提取各阶段的摘要信息
    result["stages"]["inspect"] = _parse_stage(stdout, "INSPECT")
    result["stages"]["plan"] = _parse_stage(stdout, "PLAN")
    result["stages"]["execute"] = _parse_stage(stdout, "EXECUTE")

    # 从 execute.json 提取指标
    execute_json = PROJECT_ROOT / "output" / gse_id / "execute.json"
    if execute_json.exists():
        try:
            ej = json.loads(execute_json.read_text(encoding="utf-8"))
            result["metrics"]["cells"] = ej.get("cells", 0)
            result["metrics"]["genes"] = ej.get("genes", 0)
            result["metrics"]["batches"] = ej.get("batches", 0)
            result["metrics"]["success"] = ej.get("success", False)
            result["metrics"]["errors"] = ej.get("errors", [])
            result["metrics"]["warnings"] = ej.get("warnings", [])
        except Exception:
            pass

    # 从 plan.json 提取置信度
    plan_json = PROJECT_ROOT / "output" / gse_id / "plan.json"
    if plan_json.exists():
        try:
            pj = json.loads(plan_json.read_text(encoding="utf-8"))
            result["metrics"]["confidence"] = pj.get("confidence", 0)
            result["metrics"]["field_mappings"] = len(pj.get("field_mappings", []))
            result["metrics"]["batch_separation_strategy"] = pj.get(
                "batch_separation_strategy", ""
            )
            result["metrics"]["kb_match_count"] = pj.get("kb_match_count", 0)
        except Exception:
            pass

    # 从 inspect.json 提取基本统计
    inspect_json = PROJECT_ROOT / "output" / gse_id / "inspect.json"
    if inspect_json.exists():
        try:
            ij = json.loads(inspect_json.read_text(encoding="utf-8"))
            result["metrics"]["total_files"] = ij.get("total_files", 0)
            result["metrics"]["gsm_entries"] = len(ij.get("gsm_entries", []))
            result["metrics"]["data_files"] = len(ij.get("data_files", []))
        except Exception:
            pass

    # 成功判断：execute.json 标记 true 且 cells > 0
    result["success"] = (
        result["metrics"].get("success", False)
        and result["metrics"].get("cells", 0) > 0
    )

    # 错误收集
    if not result["success"]:
        for line in (stdout + stderr).split("\n"):
            low = line.lower()
            if any(kw in low for kw in ["error", "fail", "traceback", "exception"]):
                if len(line) > 10:
                    result["errors"].append(line.strip()[:200])

    return result


def _parse_stage(stdout: str, stage: str) -> dict[str, str]:
    """从 CLI 输出中提取某个阶段的摘要行。"""
    for line in stdout.split("\n"):
        if f"[{stage}]" in line.upper() or stage.lower() in line.lower():
            return {"summary": line.strip()[:200]}
    return {}


def grade_result(r: dict[str, Any]) -> str:
    """根据指标评定等级：S / A / B / C / D / F。"""
    if not r["success"]:
        return "D"
    conf = r["metrics"].get("confidence", 0)
    warnings = len(r["metrics"].get("warnings", []))
    errors = len(r["metrics"].get("errors", []))
    if conf >= 0.9 and warnings == 0 and errors == 0:
        return "S"
    if conf >= 0.7:
        return "A"
    if conf >= 0.5:
        return "B"
    if r["returncode"] == 0:
        return "C"
    return "F"


# ---- 主入口 ----

def main() -> None:
    global TIMEOUT_PER_GSE
    import argparse

    p = argparse.ArgumentParser(description="Scrut-Cell Batch Benchmark")
    p.add_argument("--resume", action="store_true", help="跳过已有结果的数据集")
    p.add_argument("--keep-data", action="store_true", help="保留下载文件和 .h5ad")
    p.add_argument("--timeout", type=int, default=None, help="每个 GSE 超时秒数（默认无限制）")
    p.add_argument("--failed", action="store_true", help="仅重测上一次运行中失败的数据集")
    args = p.parse_args()

    run_dir = RESULTS_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    TIMEOUT_PER_GSE = args.timeout

    # 加载数据集列表（优先从 Excel，--failed 从上次失败中读取）
    datasets = load_failed_from_last_run() if args.failed else load_datasets()
    if not datasets:
        log = open(str(run_dir / "_full.log"), "w", encoding="utf-8")
        log.write("No datasets to test.\n"); log.close()
        print("No datasets to test.")
        return

    log_file = run_dir / "_full.log"
    log = open(str(log_file), "w", encoding="utf-8")

    def log_print(msg: str) -> None:
        print(msg)
        log.write(msg + "\n")
        log.flush()

    log_print("=" * 60)
    log_print(f"  Scrut-Cell Batch Benchmark")
    log_print(f"  Datasets: {len(datasets)}")
    log_print(f"  Results:  {run_dir}")
    log_print(f"  Time:     {datetime.now().isoformat()}")
    log_print("=" * 60)

    # Step 1: 清空 KB
    log_print("\n>>> Clearing knowledge base...")
    kb_out = clear_kb()
    log_print(kb_out or "(done)")

    # Step 2: 逐个测试
    results: list[dict[str, Any]] = []
    for i, ds in enumerate(datasets, 1):
        gse_id = ds["gse_id"]
        ds_id = ds["id"]
        ds_dir = run_dir / ds_id
        ds_dir.mkdir(parents=True, exist_ok=True)

        # 断点续跑
        if args.resume and (ds_dir / "_result.json").exists():
            log_print(f"\n[{i}/{len(datasets)}] SKIP {ds_id} ({gse_id}) — already done")
            try:
                results.append(json.loads((ds_dir / "_result.json").read_text()))
            except Exception:
                pass
            continue

        log_print(f"\n{'─'*50}")
        log_print(f"  [{i}/{len(datasets)}] {ds_id} — {gse_id}")
        log_print(f"  Category: {ds['category']} | {ds['label']}")
        log_print(f"  Features: {ds['features']}")

        result = test_one_gse(gse_id, keep_data=args.keep_data)
        result.update({"id": ds_id, "category": ds["category"],
                        "label": ds["label"], "features": ds["features"]})
        result["grade"] = grade_result(result)
        results.append(result)

        # 写 per-GSE 日志
        gse_log = ds_dir / f"{gse_id}.log"
        gse_log.write_text(
            f"=== STDOUT ===\n{result['stdout']}\n\n=== STDERR ===\n{result['stderr']}",
            encoding="utf-8",
        )
        (ds_dir / "_result.json").write_text(
            json.dumps({k: v for k, v in result.items() if k not in ("stdout", "stderr")},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        grade = result["grade"]
        status = "PASS" if result["success"] else "FAIL"
        log_print(f"  Result: {status} | Grade: {grade} | "
                  f"C={result['metrics'].get('cells','?')} "
                  f"G={result['metrics'].get('genes','?')} "
                  f"B={result['metrics'].get('batches','?')} | "
                  f"{result['duration_s']}s")

        # Cleanup download + extracted files to free disk space
        if not args.keep_data:
            _cleanup_gse_output(gse_id)

    # Step 3: 汇总报告
    log_print(f"\n\n{'='*60}")
    log_print(f"  BENCHMARK SUMMARY")
    log_print(f"{'='*60}")

    total = len(results)
    passed = sum(1 for r in results if r["success"])
    grades = {g: sum(1 for r in results if r["grade"] == g)
              for g in ["S", "A", "B", "C", "D", "F"]}

    log_print(f"\n  Total:   {total}")
    log_print(f"  Passed:  {passed} ({passed / total:.0%})" if total else "  Passed: 0")
    log_print(f"  Grades:  S={grades['S']} A={grades['A']} B={grades['B']} "
              f"C={grades['C']} D={grades['D']} F={grades['F']}")

    # 按类别分析
    cats: dict[str, list[dict]] = {}
    for r in results:
        cats.setdefault(r["category"], []).append(r)

    log_print(f"\n{'─'*40}")
    log_print(f"  By Category:")
    for cat_name, cat_results in sorted(cats.items()):
        cat_pass = sum(1 for r in cat_results if r["success"])
        confs = [r["metrics"].get("confidence", 0) for r in cat_results if r["success"]]
        avg_conf = sum(confs) / len(confs) if confs else 0
        log_print(f"    {cat_name:12s}: {cat_pass}/{len(cat_results)} passed "
                  f"({cat_pass/len(cat_results):.0%}) avg_conf={avg_conf:.0%}")

    # 失败列表
    failures = [r for r in results if not r["success"]]
    if failures:
        log_print(f"\n{'─'*40}")
        log_print(f"  Failures ({len(failures)}):")
        for r in failures:
            errs = "; ".join(r["errors"][:2]) if r["errors"] else "(no error details)"
            log_print(f"    {r['gse_id']} ({r['id']}): {errs[:150]}")

    # 每个数据集的详细表
    log_print(f"\n{'─'*40}")
    log_print(f"  Detail:")
    header = f"  {'ID':6s} {'GSE':12s} {'Grade':5s} {'Cells':>8s} {'Genes':>8s} {'Bat':>3s} {'Conf':>5s} {'Time':>6s}  {'Category':12s}"
    log_print(header)
    log_print("  " + "-" * (len(header) - 2))
    for r in results:
        m = r["metrics"]
        log_print(
            f"  {r['id']:6s} {r['gse_id']:12s} {r['grade']:5s} "
            f"{str(m.get('cells','?')):>8s} {str(m.get('genes','?')):>8s} "
            f"{str(m.get('batches','?')):>3s} "
            f"{str(round(m.get('confidence',0)*100))+'%':>5s} "
            f"{str(r['duration_s'])+'s':>6s}  {r['category']:12s}"
        )

    # 写 JSON 汇总
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total": total, "passed": passed, "pass_rate": f"{passed/total:.0%}" if total else "0%",
        "grades": grades,
        "by_category": {c: {"total": len(cr), "passed": sum(1 for r in cr if r["success"]),
                             "pass_rate": f"{sum(1 for r in cr if r['success'])/len(cr):.0%}"
                             if cr else "0%"}
                        for c, cr in cats.items()},
        "failures": [{"id": r["id"], "gse_id": r["gse_id"], "errors": r["errors"]}
                     for r in failures],
        "results": [
            {k: v for k, v in r.items() if k not in ("stdout", "stderr")}
            for r in results
        ],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 创建 latest 软链接
    latest_link = RESULTS_DIR / "latest"
    if latest_link.exists() or latest_link.is_symlink():
        latest_link.unlink(missing_ok=True)
    try:
        latest_link.symlink_to(run_dir.name, target_is_directory=True)
    except OSError:
        pass  # Windows 需要管理员权限创建 symlink，忽略

    log_print(f"\n{'='*60}")
    log_print(f"  Report saved: {run_dir / 'summary.json'}")
    log_print(f"{'='*60}")
    log.close()

    # 控制台简要输出
    print(f"\nResults: {passed}/{total} passed | Grades: "
          f"S={grades['S']} A={grades['A']} B={grades['B']} "
          f"C={grades['C']} D={grades['D']} F={grades['F']}")


if __name__ == "__main__":
    main()
