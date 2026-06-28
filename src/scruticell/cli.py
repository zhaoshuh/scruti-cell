"""CLI entry point for Scrut-Cell.

Usage:
    scruticell parse GSE12345
    scruticell parse GSE12345 --output ./results
    scruticell parse GSE12345 --llm-model gpt-4o-mini
    scruticell config show
    scruticell --help
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from scruticell import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scruticell")


def _get_knowledge_base():
    """Return the appropriate knowledge base based on license tier."""
    from scruticell.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
    from scruticell.license import is_enterprise

    if is_enterprise():
        from scruticell.knowledge.graph import KnowledgeGraph
        kg = KnowledgeGraph(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        kg.initialize_schema()
        return kg, kg  # returned as context manager replacement
    else:
        from scruticell.knowledge.graph import LocalKnowledgeBase
        return LocalKnowledgeBase(), None


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """Scrut-Cell: GEO single-cell data standardization pipeline.

    Converts NCBI GEO GSE accessions into analysis-ready AnnData objects
    with standardized metadata, unified gene names, aligned cell barcodes,
    and identified batches.
    """
    pass


@main.command()
@click.argument("gse_id")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for processed AnnData file.",
)
@click.option(
    "--llm-model",
    default=None,
    help="LLM model to use (default: deepseek-chat).",
)
@click.option(
    "--llm-api-key",
    default=None,
    help="API key for LLM provider (env: DEEPSEEK_API_KEY or OPENAI_API_KEY).",
)
@click.option(
    "--llm-base-url",
    default=None,
    help="Base URL for LLM API (default: https://api.deepseek.com).",
)
@click.option(
    "--no-literature",
    is_flag=True,
    help="Skip PubMed literature search.",
)
@click.option(
    "--max-retries",
    type=int,
    default=3,
    help="Maximum reflexion retries on low confidence (default: 3).",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Enable verbose output.",
)
@click.option(
    "--keep-data",
    is_flag=True,
    help="Keep downloaded files and output .h5ad (default: clean up).",
)
def parse(
    gse_id: str,
    output: Optional[Path],
    llm_model: Optional[str],
    llm_api_key: Optional[str],
    llm_base_url: Optional[str],
    no_literature: bool,
    max_retries: int,
    verbose: bool,
    keep_data: bool,
) -> None:
    """Parse a GSE accession into an AnnData object.

    GSE_ID: NCBI GEO Series accession (e.g., GSE12345).
    """
    if verbose:
        logging.getLogger("scruticell").setLevel(logging.DEBUG)

    import scruticell.config as cfg
    if llm_model:
        cfg.LLM_MODEL = llm_model
    if llm_api_key:
        cfg.LLM_API_KEY = llm_api_key
    if llm_base_url:
        cfg.LLM_BASE_URL = llm_base_url

    output_dir = output or Path.cwd()

    click.echo(f"\n{'='*60}")
    click.echo(f"  Scrut-Cell Pipeline")
    click.echo(f"  GSE: {gse_id}")
    click.echo(f"  Output: {output_dir}")
    click.echo(f"{'='*60}")

    from scruticell.pipeline.state import PipelineState
    from scruticell.pipeline.nodes.download import download_node
    from scruticell.pipeline.nodes.parse import parse_soft_node
    from scruticell.pipeline.nodes.matrix import discover_matrix_node, read_matrix_node
    from scruticell.pipeline.nodes.literature import literature_node
    from scruticell.pipeline.nodes.alignment import align_cells_node, unify_genes_node, output_node
    from scruticell.pipeline.nodes.validation import validate_node
    from scruticell.pipeline.nodes.reflexion import (
        reflexion_node, apply_strategy_to_state, set_knowledge_base,
    )
    from scruticell.utils.audit import AuditLogger

    kb, _ = _get_knowledge_base()
    set_knowledge_base(kb)

    audit = AuditLogger(output_dir, gse_id)
    audit.log_step("pipeline", "started", {"gse_id": gse_id, "max_retries": max_retries})

    state = PipelineState(gse_id=gse_id, output_dir=output_dir, max_retries=max_retries)

    download_stages = [
        ("Download", download_node),
        ("Parse SOFT", parse_soft_node),
    ]
    if not no_literature:
        download_stages.append(("Literature", literature_node))

    align_stages = [
        ("Discover Matrix", discover_matrix_node),
        ("Read Matrix", read_matrix_node),
        ("Align Cells", align_cells_node),
        ("Unify Genes", unify_genes_node),
        ("Validate", validate_node),
    ]

    def run_stages(stage_list, run_state):
        for stage_name, node_fn in stage_list:
            click.echo(f"  [{stage_name}]...", nl=False)
            try:
                result = node_fn(run_state)
                for key, value in result.items():
                    setattr(run_state, key, value)
                audit.log_step(stage_name.lower().replace(" ", "_"), "completed")
            except Exception as exc:
                click.echo(f" FAILED: {exc}")
                audit.log_step(stage_name.lower().replace(" ", "_"), "failed", {"error": str(exc)})
                logger.exception("Stage '%s' failed", stage_name)
                raise SystemExit(1)
            if run_state.errors:
                click.echo(" WARNING")
            else:
                click.echo(" OK")

    run_stages(download_stages, state)

    for retry in range(max_retries + 1):
        if retry > 0:
            click.echo(f"\n  --- Reflexion retry {retry}/{max_retries} ---")
            click.echo(f"  [Reflexion]...", nl=False)
            result = reflexion_node(state)
            for key, value in result.items():
                setattr(state, key, value)
            click.echo(" OK")

            strategy = state.current_strategy
            if strategy.get("abort"):
                click.echo(f"  Strategy suggests abort: {strategy.get('notes', '')}")
                break
            if not strategy or strategy.get("action") != "retry":
                click.echo(f"  No actionable strategy, stopping retries")
                break
            apply_strategy_to_state(state, strategy)
            click.echo(f"  Strategy: {strategy.get('notes', 'retry')}")

        run_stages(align_stages, state)

        if state.validation_passed:
            break

    if state.validation_passed:
        click.echo(f"\n  [Output]...", nl=False)
        try:
            result = output_node(state)
            for key, value in result.items():
                setattr(state, key, value)
            click.echo(" OK")
        except Exception as exc:
            click.echo(f" FAILED: {exc}")
            logger.exception("Output stage failed")
            raise SystemExit(1)
    else:
        click.echo(f"\n  [Output] SKIPPED (validation failed after {state.retry_count} retries)")

    scores = state.confidence_scores
    click.echo(f"\n{'='*60}")
    click.echo(f"  Pipeline complete")
    click.echo(f"  Output: {state.output_path}")
    click.echo(f"  Cells: {state.obs.shape[0] if state.obs is not None else 0}")
    click.echo(f"  Genes: {state.var.shape[0] if state.var is not None else 0}")
    click.echo(f"  Organism: {state.organism or 'unknown'}")
    click.echo(f"  Retries: {state.retry_count}")
    click.echo(f"  Confidence: {state.overall_confidence():.0%}")
    if scores:
        for k, v in scores.items():
            click.echo(f"    {k}: {v:.0%}")
    if state.reflexion_notes:
        click.echo(f"  Reflexion:")
        for n in state.reflexion_notes[-3:]:
            click.echo(f"    - {n}")
    if state.alignment_warnings:
        click.echo(f"  Warnings ({len(state.alignment_warnings)}):")
        for w in state.alignment_warnings[:5]:
            click.echo(f"    - {w}")
    click.echo(f"{'='*60}")

    audit.log_confidence(state.confidence_scores)
    audit.log_step("pipeline", "completed", {
        "output": str(state.output_path) if state.output_path else "skipped",
        "cells": state.obs.shape[0] if state.obs is not None else 0,
        "genes": state.var.shape[0] if state.var is not None else 0,
        "retries": state.retry_count,
        "confidence": state.overall_confidence(),
    })
    audit_path = audit.write()
    click.echo(f"  Audit log: {audit_path}\n")

    # Cleanup: delete downloaded data and .h5ad
    if not keep_data:
        import shutil
        download_dir = Path.cwd() / "output" / gse_id / "download"
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)
            click.echo(f"  Cleaned up download cache: {download_dir}")
        if state.output_path and Path(state.output_path).exists():
            Path(state.output_path).unlink(missing_ok=True)
            click.echo(f"  Cleaned up output .h5ad: {state.output_path}")
        click.echo(f"  Preserved: audit log, KB strategies")


@main.command()
def config() -> None:
    """Show current configuration."""
    from scruticell.config import (
        CONFIDENCE_THRESHOLD_AUTO,
        CONFIDENCE_THRESHOLD_WARN,
        DATA_DIR,
        DOWNLOAD_RETRIES,
        DOWNLOAD_TIMEOUT,
        LLM_MODEL,
        LLM_TEMPERATURE,
        LLM_BASE_URL,
        LLM_API_KEY,
        NEO4J_URI,
    )

    click.echo("Scrut-Cell Configuration:")
    click.echo(f"  Data directory:     {DATA_DIR}")
    click.echo(f"  LLM model:          {LLM_MODEL}")
    click.echo(f"  LLM temperature:    {LLM_TEMPERATURE}")
    click.echo(f"  LLM base URL:       {LLM_BASE_URL}")
    click.echo(f"  LLM API key:        {'***' if LLM_API_KEY else '(not set)'}")
    click.echo(f"  Neo4j URI:          {NEO4J_URI}")
    click.echo(f"  Download timeout:   {DOWNLOAD_TIMEOUT}s")
    click.echo(f"  Download retries:   {DOWNLOAD_RETRIES}")
    click.echo(f"  Confidence auto:    {CONFIDENCE_THRESHOLD_AUTO}")
    click.echo(f"  Confidence warn:    {CONFIDENCE_THRESHOLD_WARN}")


@main.command()
@click.confirmation_option(prompt="Delete all failure case records from knowledge base?")
def db_clear() -> None:
    """Clear all stored strategy/failure records from the knowledge base."""
    kb, _ = _get_knowledge_base()
    if hasattr(kb, "clear_failures"):
        n = kb.clear_failures()
        click.echo(f"Deleted {n} failure case record(s) from knowledge base.")
    else:
        click.echo("Knowledge base backend does not support clear operation.")


@main.command()
@click.argument("gse_id")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for the inspection report JSON.",
)
@click.option(
    "--no-download",
    is_flag=True,
    help="Use existing download directory (skip download).",
)
def inspect(
    gse_id: str,
    output: Optional[Path],
    no_download: bool,
) -> None:
    """Inspect a GSE dataset and generate a structured diagnostic report.

    Produces a JSON report containing:
    - File decompression tree
    - Content summaries for every file (preview rows, shapes, columns)
    - GSE and GSM metadata
    - Candidate matrix and metadata file lists

    Designed as the first stage of intelligent parsing: the report
    is structured for LLM consumption to plan batch-matrix-metadata
    processing.
    """
    import json
    from scruticell.intelligence import Reporter
    from scruticell.pipeline.nodes.download import download_node
    from scruticell.pipeline.nodes.parse import parse_soft_node
    from scruticell.pipeline.state import PipelineState

    state = PipelineState(gse_id=gse_id, output_dir=Path.cwd())

    if not no_download:
        click.echo("Downloading...")
        result = download_node(state)
        for k, v in result.items():
            setattr(state, k, v)
        if state.download_errors:
            click.echo(f"  Download errors: {state.download_errors}")
    else:
        state.download_dir = Path.cwd() / "output" / gse_id / "download"
        if not state.download_dir.exists():
            state.download_dir = Path.cwd() / gse_id
        # Populate supplementary files from existing download
        if state.download_dir.exists():
            _H5AD_EXTS = {".h5ad", ".h5", ".mtx", ".mtx.gz", ".csv", ".csv.gz",
                          ".tsv", ".tsv.gz", ".rds", ".rdata", ".loom", ".txt", ".txt.gz"}
            state.supplementary_files = [
                p for p in state.download_dir.iterdir()
                if p.suffix.lower() in _H5AD_EXTS
                or any(p.name.lower().endswith(ext) for ext in [".soft.gz", ".soft"])
            ]
            # Set soft file
            for sf in state.supplementary_files:
                if ".soft" in sf.name.lower():
                    state.soft_file = sf
                    break
        # Rebuild file_gsm_map from cached SOFT file
        try:
            from scruticell.pipeline.nodes.download import _extract_supplementary_refs
            refs = _extract_supplementary_refs(gse_id)
            state.file_gsm_map = {str(r["filename"]): str(r["gsm"]) for r in refs}
        except Exception:
            state.file_gsm_map = {}

    click.echo("Parsing SOFT metadata...")
    try:
        result = parse_soft_node(state)
        for k, v in result.items():
            setattr(state, k, v)
    except Exception as exc:
        click.echo(f"  Parse warning: {exc}")

    click.echo("Building inspection report...")
    reporter = Reporter()
    report = reporter.build_report(
        gse_id=gse_id,
        download_dir=state.download_dir or (Path.cwd() / "output" / gse_id / "download"),
        gsm_entries=state.parsed_characteristics,
        gse_metadata=state.raw_metadata,
        file_gsm_map=getattr(state, "file_gsm_map", {}),
    )

    json_str = report.to_json()

    if output:
        output_path = Path(output)
        if output_path.is_dir():
            output_path = output_path / f"{gse_id}_report.json"
    else:
        output_path = Path.cwd() / "output" / gse_id / "inspect.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_str, encoding="utf-8")
    click.echo(f"\nReport written to: {output_path}")

    click.echo(f"\n{'='*60}")
    click.echo(f"  Inspection Report: {gse_id}")
    click.echo(f"  Files inspected:   {report.total_files}")
    click.echo(f"  Data files:         {len(report.data_files)}")
    click.echo(f"  GSM entries:        {len(report.gsm_entries)}")
    if report.errors:
        click.echo(f"  Errors:            {len(report.errors)}")
        for err in report.errors:
            click.echo(f"    - {err}")
    click.echo(f"{'='*60}")


@main.command()
@click.argument("gse_id")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path for the processing plan JSON.",
)
@click.option(
    "--report",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to an existing inspection report JSON (skip download+inspect).",
)
@click.option(
    "--llm-model",
    default=None,
    help="LLM model for plan generation (default: from config).",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help="Skip LLM plan generation; only show KB matches.",
)
def plan(
    gse_id: str,
    output: Optional[Path],
    report: Optional[Path],
    llm_model: Optional[str],
    no_llm: bool,
) -> None:
    """Generate a batch-matrix-metadata processing plan using LLM.

    First inspects the dataset (or loads an existing report), queries
    the knowledge graph for similar known cases, and uses LLM to
    produce a structured ProcessingPlan mapping matrices to batches
    and standardizing metadata fields.

    The plan can then be used by the intelligent parse pipeline
    (M3) to execute the processing.
    """
    import json
    from scruticell.intelligence import (
        GseReport,
        PlanOrchestrator,
        Reporter,
        StrategyMatcher,
    )
    from scruticell.pipeline.nodes.download import download_node
    from scruticell.pipeline.nodes.parse import parse_soft_node
    from scruticell.pipeline.state import PipelineState

    # Load or generate report
    if report:
        click.echo(f"Loading report: {report}")
        report_json = report.read_text(encoding="utf-8")
        report_data = json.loads(report_json)
        gse_report = GseReport.from_dict(report_data)
    else:
        # Try default output path first (from prior 'inspect' run)
        default_report = Path.cwd() / "output" / gse_id / "inspect.json"
        if default_report.exists():
            click.echo(f"Found existing report: {default_report}")
            report_data = json.loads(default_report.read_text(encoding="utf-8"))
            gse_report = GseReport.from_dict(report_data)
        else:
            click.echo(f"No report found at {default_report}, generating...")
            click.echo("Downloading...")
            state = PipelineState(gse_id=gse_id, output_dir=Path.cwd())
            result = download_node(state)
            for k, v in result.items():
                setattr(state, k, v)
            if state.download_errors:
                for err in state.download_errors:
                    click.echo(f"  {err}")

            click.echo("Parsing SOFT metadata...")
            try:
                result = parse_soft_node(state)
                for k, v in result.items():
                    setattr(state, k, v)
            except Exception as exc:
                click.echo(f"  Parse warning: {exc}")

            click.echo("Building inspection report...")
            reporter = Reporter()
            gse_report = reporter.build_report(
                gse_id=gse_id,
                download_dir=state.download_dir or (Path.cwd() / "output" / gse_id / "download"),
                gsm_entries=state.parsed_characteristics,
                gse_metadata=state.raw_metadata,
            )

    click.echo(f"\n{'='*60}")
    click.echo(f"  Plan Generation: {gse_id}")
    click.echo(f"  Files: {gse_report.total_files}")
    click.echo(f"  Data files:   {len(gse_report.data_files)}")
    click.echo(f"  GSM entries: {len(gse_report.gsm_entries)}")

    # KB matching and plan generation
    kb, _ = _get_knowledge_base()
    matcher = StrategyMatcher(kb)
    matches = matcher.find_similar(gse_report)
    if matches:
        click.echo(f"\n  KB Matches: {len(matches)}")
        for m in matches[:3]:
            click.echo(f"    - {m.source_gse_id} (score: {m.similarity_score:.2f}, "
                       f"success: {m.success_count}, fail: {m.fail_count})")
    else:
        click.echo("\n  KB Matches: 0 (no similar cases found)")

    if no_llm:
        click.echo("\n  Skipping LLM plan generation (--no-llm)")
        return

    click.echo("\n  Generating plan via LLM...")
    orch = PlanOrchestrator(matcher)
    plan = orch.plan(gse_report)

    if plan is None or not plan.is_valid():
        click.echo("  FAILED: Could not generate a valid plan")
        return

    plan_json = plan.to_json()
    click.echo(f"  Plan generated: confidence={plan.confidence:.0%}, "
               f"matrices={len(plan.matrices)}, batches={len(plan.batches)}")

    if plan.strategy_notes:
        click.echo(f"\n  Strategy notes:")
        for line in plan.strategy_notes.split("\n"):
            click.echo(f"    {line}")

    if output:
        output_path = Path(output)
        if output_path.is_dir():
            output_path = output_path / f"{gse_id}_plan.json"
    else:
        output_path = Path.cwd() / "output" / gse_id / "plan.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan_json, encoding="utf-8")
    click.echo(f"\n  Plan written to: {output_path}")

    click.echo(f"\n{'='*60}")


@main.command()
@click.argument("gse_id")
@click.option(
    "--output", "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output directory for AnnData file.",
)
@click.option(
    "--plan-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to processing plan JSON (default: output/<GSE_ID>/plan.json).",
)
@click.option(
    "--report-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to inspection report JSON (default: output/<GSE_ID>/inspect.json).",
)
@click.option(
    "--no-download",
    is_flag=True,
    help="Use existing download directory (skip download).",
)
@click.option(
    "--reflect/--no-reflect",
    default=True,
    help="Enable LLM reflection on execution failure (default: on).",
)
@click.option(
    "--keep-data",
    is_flag=True,
    help="Keep downloaded files and output .h5ad (default: delete, keep only strategies and logs).",
)
def execute(
    gse_id: str,
    output: Optional[Path],
    plan_file: Optional[Path],
    report_file: Optional[Path],
    no_download: bool,
    reflect: bool,
    keep_data: bool,
) -> None:
    """Execute a processing plan to produce an AnnData file.

    Takes a processing plan and inspection report (from the 'plan'
    and 'inspect' commands), reads the specified matrices, applies
    batch and field mappings, and writes the output .h5ad.

    By default, downloaded files and .h5ad are deleted after execution,
    keeping only strategies (plan.json, provenance.json). Use --keep-data
    to preserve everything.
    """
    import json
    from scruticell.intelligence import (
        ExecutionMonitor,
        GseReport,
        PlanExecutor,
        ProcessingPlan,
        StrategyMatcher,
    )
    from scruticell.pipeline.nodes.download import download_node
    from scruticell.pipeline.nodes.parse import parse_soft_node
    from scruticell.pipeline.state import PipelineState

    click.echo(f"\n{'='*60}")
    click.echo(f"  Plan Execution: {gse_id}")
    click.echo(f"{'='*60}")

    # Resolve plan and report files to default paths if not specified
    default_base = Path.cwd() / "output" / gse_id
    if plan_file is None:
        plan_file = default_base / "plan.json"
    if report_file is None:
        report_file = default_base / "inspect.json"

    if not plan_file.exists():
        click.echo(f"Error: plan file not found: {plan_file}")
        click.echo(f"  Run 'scruticell plan {gse_id}' first.")
        raise SystemExit(1)
    if not report_file.exists():
        click.echo(f"Error: report file not found: {report_file}")
        click.echo(f"  Run 'scruticell inspect {gse_id}' first.")
        raise SystemExit(1)

    click.echo(f"  Plan:   {plan_file}")
    click.echo(f"  Report: {report_file}")

    # Load plan and report
    plan_json = plan_file.read_text(encoding="utf-8")
    plan_data = json.loads(plan_json)
    plan = ProcessingPlan.from_llm_response(gse_id, json.dumps(plan_data))

    report_json = report_file.read_text(encoding="utf-8")
    report_data = json.loads(report_json)
    report = GseReport.from_dict(report_data)

    output_dir = output or (Path.cwd() / "output" / gse_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Setup state
    state = PipelineState(gse_id=gse_id, output_dir=output_dir)
    if not no_download:
        download_dir = Path.cwd() / "output" / gse_id / "download"
        if download_dir.exists() and any(download_dir.iterdir()):
            # Already downloaded (e.g. by prior 'inspect'), reuse
            click.echo("[Download]... SKIPPED (already cached)")
            state.download_dir = download_dir
            _EXTS = {".h5ad", ".h5", ".mtx", ".mtx.gz", ".csv", ".csv.gz",
                     ".tsv", ".tsv.gz", ".rds", ".rdata", ".loom", ".txt", ".txt.gz",
                     ".tar.gz", ".tar", ".tgz", ".gz", ".zip"}
            state.supplementary_files = [
                p for p in download_dir.iterdir()
                if p.suffix.lower() in _EXTS
                or any(p.name.lower().endswith(ext) for ext in [".soft.gz", ".soft"])
            ]
            for sf in state.supplementary_files:
                if ".soft" in sf.name.lower():
                    state.soft_file = sf
                    break
            if state.soft_file:
                click.echo("[Parse]...", nl=False)
                try:
                    result = parse_soft_node(state)
                    for k, v in result.items():
                        setattr(state, k, v)
                    click.echo(" OK")
                except Exception as exc:
                    click.echo(f" WARNING: {exc}")
        else:
            click.echo("[Download]...", nl=False)
            result = download_node(state)
            for k, v in result.items():
                setattr(state, k, v)
            if state.download_errors:
                for err in state.download_errors:
                    click.echo(f"  {err}")
            click.echo(" OK")
            click.echo("[Parse]...", nl=False)
            try:
                result = parse_soft_node(state)
                for k, v in result.items():
                    setattr(state, k, v)
                click.echo(" OK")
            except Exception as exc:
                click.echo(f" WARNING: {exc}")
    else:
        state.download_dir = Path.cwd() / "output" / gse_id / "download"
        if not state.download_dir.exists():
            state.download_dir = Path.cwd() / gse_id
        if state.download_dir.exists():
            _H5AD_EXTS = {".h5ad", ".h5", ".mtx", ".mtx.gz", ".csv", ".csv.gz",
                          ".tsv", ".tsv.gz", ".rds", ".rdata", ".loom", ".txt", ".txt.gz",
                          ".tar.gz", ".tar", ".tgz", ".gz", ".zip"}
            state.supplementary_files = [
                p for p in state.download_dir.iterdir()
                if p.suffix.lower() in _H5AD_EXTS
                or any(p.name.lower().endswith(ext) for ext in [".soft.gz", ".soft"])
            ]
            for sf in state.supplementary_files:
                if ".soft" in sf.name.lower():
                    state.soft_file = sf
                    break
            if state.soft_file:
                click.echo("[Parse]...", nl=False)
                try:
                    result = parse_soft_node(state)
                    for k, v in result.items():
                        setattr(state, k, v)
                    click.echo(" OK")
                except Exception as exc:
                    click.echo(f" WARNING: {exc}")

    # Execute
    if reflect:
        click.echo("\nExecuting with LLM reflection...")
        monitor = ExecutionMonitor()
        result = monitor.execute_with_reflection(report, plan, output_dir, state)
    else:
        click.echo("\nExecuting...")
        executor = PlanExecutor()
        result = executor.execute(report, plan, output_dir, state)

    # Execute summary: provenance + trace + stats
    execute_path = output_dir / "execute.json"
    trace_text = getattr(result, "_trace_text", "")
    execute_data = {
        "gse_id": gse_id,
        "success": result.success,
        "output_path": str(result.output_path) if result.output_path else None,
        "cells": result.n_cells,
        "genes": result.n_genes,
        "batches": result.n_batches,
        "steps": [
            {"step": s.step, "status": s.status, "details": s.details, "error": s.error}
            for s in result.steps
        ],
        "errors": list(result.errors) if result.errors else [],
        "warnings": list(result.warnings) if result.warnings else [],
        "trace": trace_text if trace_text else None,
    }
    execute_path.write_text(
        json.dumps(execute_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Metadata markdown — per-batch summary table
    batch_meta = getattr(result, "batch_metadata", []) or []
    if batch_meta:
        metadata_path = output_dir / "metadata.md"
        # Collect all obs keys across batches for table header
        all_keys: list[str] = []
        for bm in batch_meta:
            for k in bm:
                if k not in ("file", "cells", "genes") and k not in all_keys:
                    all_keys.append(k)
        header_cols = ["File", "Cells", "Genes"] + all_keys
        lines = [
            f"# {gse_id} — Metadata Summary",
            "",
            "| " + " | ".join(header_cols) + " |",
            "|" + "|".join(["------"] * len(header_cols)) + "|",
        ]
        for bm in batch_meta:
            row = [str(bm.get(c, "")) for c in ["file", "cells", "genes"] + all_keys]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        lines.append(
            f"**Total**: {len(batch_meta)} batches | "
            f"{result.n_cells} cells | {result.n_genes} genes"
        )
        metadata_path.write_text("\n".join(lines), encoding="utf-8")
        click.echo(f"  Metadata:  {metadata_path}")

    # Update KB strategy stats
    kb_exec, _ = _get_knowledge_base()
    matcher = StrategyMatcher(kb_exec)
    matcher.update_strategy_stats(report, result.success)

    # Report
    click.echo(f"\n{'='*60}")
    click.echo(f"  Execution: {'SUCCESS' if result.success else 'FAILED'}")
    click.echo(f"  Output:   {result.output_path}")
    click.echo(f"  Cells:    {result.n_cells}")
    click.echo(f"  Genes:    {result.n_genes}")
    click.echo(f"  Batches:  {result.n_batches}")
    click.echo(f"  Provenance: {execute_path}")

    # Show processing strategies (Step 4 + Step 5 from multi-step extraction)
    if plan.processing_code:
        code_lines = [l for l in plan.processing_code.strip().split("\n") if l.strip()]
        click.echo(f"\n  [Step 4 — Metadata Strategy] {len(code_lines)} lines of Python")
        for line in code_lines[:8]:
            click.echo(f"    {line[:100]}")
        if len(code_lines) > 8:
            click.echo(f"    ... ({len(code_lines) - 8} more lines)")

    if plan.assembly_code:
        code_lines = [l for l in plan.assembly_code.strip().split("\n") if l.strip()]
        click.echo(f"\n  [Step 5 — Assembly Strategy] {len(code_lines)} lines of Python")
        for line in code_lines[:6]:
            click.echo(f"    {line[:100]}")
        if len(code_lines) > 6:
            click.echo(f"    ... ({len(code_lines) - 6} more lines)")

    if result.warnings:
        click.echo(f"  Warnings: {len(result.warnings)}")
        for w in result.warnings[:5]:
            click.echo(f"    - {w}")
    if result.errors:
        click.echo(f"  Errors:   {len(result.errors)}")
        for e in result.errors:
            click.echo(f"    - {e}")
    click.echo(f"{'='*60}")

    # Origin trace report
    if trace_text:
        click.echo(trace_text)

    # Cleanup: delete downloaded data and .h5ad, keep only strategies and logs
    if not keep_data:
        import shutil
        # Delete download directory
        download_dir = Path.cwd() / "output" / gse_id / "download"
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)
            click.echo(f"\n  Cleaned up download cache: {download_dir}")
        # Delete output .h5ad (keep plan/provenance/report)
        if result.output_path and Path(result.output_path).exists():
            Path(result.output_path).unlink(missing_ok=True)
            click.echo(f"  Cleaned up output .h5ad: {result.output_path}")
        click.echo(f"  Preserved: plan.json, execute.json, report.json")


if __name__ == "__main__":
    main()
