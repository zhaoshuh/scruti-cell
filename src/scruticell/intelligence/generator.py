"""LLM-driven plan generation and self-validation.

PlanGenerator: takes a GseReport, queries the strategy matcher,
and uses LLM to generate a ProcessingPlan.

PlanValidator: checks the plan for consistency, triggers
re-generation if issues found.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from scruticell.intelligence.extractors import MultiStepExtractor
from scruticell.intelligence.matcher import MatchedStrategy, StrategyMatcher
from scruticell.intelligence.planner import (
    PLAN_GENERATION_SYSTEM_PROMPT,
    ProcessingPlan,
    build_plan_prompt,
    build_validation_prompt,
)
from scruticell.intelligence.reporter import GseReport
from scruticell.utils.tracing import traceable

logger = logging.getLogger(__name__)

_MAX_VALIDATION_ROUNDS = 3


class PlanGenerator:
    """Generate a ProcessingPlan from a GseReport using LLM.

    Usage:
        gen = PlanGenerator(matcher)
        plan = gen.generate(report)

    If the KB has matching strategies, they are included
    as context for the LLM.
    """

    def __init__(self, matcher: StrategyMatcher | None = None):
        self._matcher = matcher or StrategyMatcher()

    def generate(
        self,
        report: GseReport,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> ProcessingPlan | None:
        """Generate a processing plan. Traced via LangSmith."""
        return self._generate_impl(report, model, temperature)

    @traceable(name="PlanGenerator.generate", tags=["intelligence", "planning"])
    def _generate_impl(
        self,
        report: GseReport,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> ProcessingPlan | None:
        # 1. Find similar strategies
        matched: list[MatchedStrategy] = []
        try:
            matched = self._matcher.find_similar(report)
        except Exception as exc:
            logger.warning("Strategy matching skipped: %s", exc)

        # 2. Build prompt
        report_json = report.to_json()
        prompt = build_plan_prompt(report_json)

        if matched:
            strategies_text = "\n\n".join(
                f"Similar past case ({m.source_gse_id}, score={m.similarity_score:.2f}):\n{m.plan_json}"
                for m in matched[:3]
            )
            prompt += f"\n\nPAST SIMILAR CASES (use as reference):\n{strategies_text}"

        # 3. LLM call
        response = self._call_llm(prompt, model, temperature)
        if response is None:
            return None

        # 4. Parse
        try:
            plan = ProcessingPlan.from_llm_response(report.gse_id, response)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to parse LLM plan: %s", exc)
            logger.debug("Raw LLM response: %s", response[:500])
            return None

        return plan

    def _call_llm(
        self, prompt: str, model: str | None = None, temperature: float = 0.0
    ) -> str | None:
        """Call LLM with the plan generation prompt."""
        try:
            from langchain_openai import ChatOpenAI

            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE

            llm_kwargs: dict[str, Any] = {
                "model": model or LLM_MODEL,
                "temperature": temperature or LLM_TEMPERATURE,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY

            llm = ChatOpenAI(**llm_kwargs)
            messages = [
                ("system", PLAN_GENERATION_SYSTEM_PROMPT),
                ("human", prompt),
            ]
            response = llm.invoke(messages)
            content = str(response.content).strip()

            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            return content
        except Exception as exc:
            logger.warning("LLM plan generation failed: %s", exc)
            return None


class PlanValidator:
    """Validate a ProcessingPlan for internal consistency.

    Runs structural checks and optionally calls LLM for revision.
    """

    def validate(
        self, plan: ProcessingPlan, report: GseReport
    ) -> list[str]:
        """Validate a plan. Returns list of issues (empty = valid)."""
        issues: list[str] = []

        # 1. Matrix file keys must exist in report. Auto-correct short names to full ancestry keys.
        report_keys = set(report.file_summaries.keys())
        all_matrix_keys = set()
        file_key_map: dict[str, str] = {}  # old_key -> corrected_key
        for m in plan.matrices:
            if m.file_key not in report_keys:
                found = False
                for rk in report_keys:
                    if m.file_key in rk or rk.endswith(m.file_key):
                        file_key_map[m.file_key] = rk
                        m.file_key = rk
                        found = True
                        break
                if not found:
                    issues.append(f"Matrix file_key '{m.file_key}' not found in report")
            all_matrix_keys.add(m.file_key)

        # Also correct batch file_keys using the same mapping
        for b in plan.batches:
            b.file_keys = [file_key_map.get(fk, fk) for fk in b.file_keys]

        # 2. Every matrix should be assigned to a batch
        for m in plan.matrices:
            assigned = any(m.file_key in b.file_keys for b in plan.batches)
            if not assigned:
                issues.append(f"Matrix '{m.file_key}' not assigned to any batch")

        # 2b. Every batch should have a reasoning (soft check)
        missing_reasoning = [b.gsm_id for b in plan.batches if not b.reasoning]
        if missing_reasoning:
            issues.append(
                f"Batch(es) {', '.join(missing_reasoning)} missing 'reasoning' — "
                f"explain why each batch is separate"
            )

        # 2c. Batch separation strategy should be set
        valid_strategies = {
            "per_sample", "per_condition", "per_timepoint",
            "per_treatment", "per_patient", "by_gsm_file_map",
            "by_barcode_prefix", "field_combination", "single_batch", "",
        }
        if (plan.batch_separation_strategy
                and plan.batch_separation_strategy not in valid_strategies):
            issues.append(
                f"Unknown batch_separation_strategy: '{plan.batch_separation_strategy}'"
            )

        # 3. GSM IDs in batches should exist in report
        report_gsm_ids = {g.gsm_id for g in report.gsm_entries}
        for b in plan.batches:
            if b.gsm_id not in report_gsm_ids:
                issues.append(f"Batch GSM '{b.gsm_id}' not found in report GSM entries")

        # 4. Cell counts should be positive
        for b in plan.batches:
            if b.cell_count <= 0:
                # Try to infer from matrix shape
                for m in plan.matrices:
                    if m.file_key in b.file_keys:
                        summary = report.file_summaries.get(m.file_key, {})
                        shape = summary.get("h5_shape")
                        if shape:
                            b.cell_count = shape[0]
                        break

        # 5. Gene ID system should be plausible
        valid_systems = {"symbol", "ensembl_gene", "ensembl", "ensembl_gene_id", "entrez", "unknown", ""}
        if plan.gene_id_system not in valid_systems:
            issues.append(f"Unknown gene_id_system: '{plan.gene_id_system}'")

        # 6. Confidence should be reasonable
        if plan.confidence < 0.0 or plan.confidence > 1.0:
            issues.append(f"Confidence {plan.confidence} out of range [0, 1]")

        return issues

    def validate_with_llm(
        self,
        plan: ProcessingPlan,
        report: GseReport,
        issues: list[str],
        model: str | None = None,
    ) -> ProcessingPlan | None:
        """Use LLM to fix validation issues. Returns revised plan or None."""
        if not issues:
            return plan

        prompt = build_validation_prompt(
            plan_json=plan.to_json(),
            report_json=report.to_json(),
            issues=json.dumps(issues, indent=2),
        )

        try:
            from langchain_openai import ChatOpenAI

            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

            llm_kwargs: dict[str, Any] = {
                "model": model or LLM_MODEL,
                "temperature": 0.0,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY

            llm = ChatOpenAI(**llm_kwargs)
            response = llm.invoke(prompt)
            content = str(response.content).strip()

            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            plan = ProcessingPlan.from_llm_response(report.gse_id, content)
            return plan
        except Exception as exc:
            logger.warning("LLM validation failed: %s", exc)
            return None


class PlanOrchestrator:
    """Orchestrate plan generation with validation loop and literature enrichment.

    Flow:
    1. Generate initial plan via LLM
    2. Validate and self-correct (up to N rounds)
    3. If literature available: fetch PubMed, LLM-extract metadata,
       enrich the report, and re-generate plan with enriched context
    4. Re-validate final plan
    5. Store successful plan in KB

    Usage:
        orch = PlanOrchestrator(matcher)
        plan = orch.plan(report)
        if plan and plan.is_valid():
            # execute plan
    """

    def __init__(self, matcher: StrategyMatcher | None = None):
        self._matcher = matcher or StrategyMatcher()
        self._generator = PlanGenerator(self._matcher)
        self._validator = PlanValidator()

    @traceable(name="PlanOrchestrator.plan", tags=["intelligence", "orchestration"])
    def plan(self, report: GseReport, use_multi_step: bool = True) -> ProcessingPlan | None:
        """Generate and validate a plan. Uses 6-step extraction by default.

        Args:
            report: GseReport from inspection.
            use_multi_step: True = 5 sub-task extraction + 1 final plan (default).
                           False = single monolithic prompt (fallback).
        """
        if use_multi_step:
            return self._plan_multi_step(report)
        else:
            return self._plan_single_prompt(report)

    def _plan_multi_step(self, report: GseReport) -> ProcessingPlan | None:
        """Multi-step: 5-step continuous conversation → final plan."""
        try:
            extractor = MultiStepExtractor()
            extractor.extract_all(report)         # Steps 1-5, one conversation

            plan_data = extractor.generate_final_plan(report)  # Step 5 output

            if plan_data is None or not isinstance(plan_data, dict):
                logger.error("Multi-step extraction failed to produce valid plan")
                return None

            # Inject metadata_code from Step 4 into the plan
            if extractor.metadata_code and "processing_code" not in plan_data:
                plan_data["processing_code"] = extractor.metadata_code

            # Inject Step 2 field_mappings into the plan (LLM context only, not in output schema)
            gsm_extract = extractor._context.get("gsm_extract", {})
            if isinstance(gsm_extract, dict) and not plan_data.get("field_mappings"):
                plan_data["field_mappings"] = gsm_extract.get("field_mappings", [])

            # Inject Step 2 batch_keys as defining_fields hints for batches
            if isinstance(gsm_extract, dict):
                batch_keys = gsm_extract.get("batch_keys", [])
                if batch_keys and not any(b.get("defining_fields") for b in plan_data.get("batches", [])):
                    for b in plan_data.get("batches", []):
                        if not b.get("defining_fields"):
                            b["defining_fields"] = batch_keys

            # Inject Step 3 filename_extractions and axis_metadata
            file_extract = extractor._context.get("file_extract", {})
            if isinstance(file_extract, dict):
                if not plan_data.get("_filename_extractions"):
                    plan_data["_filename_extractions"] = file_extract.get("filename_extractions", [])
                if not plan_data.get("_axis_metadata"):
                    plan_data["_axis_metadata"] = file_extract.get("axis_metadata", [])

            # Step 4 literature verified data (from extractor._context)
            lit_verified = extractor._context.get("literature_verified", {})
            if lit_verified and isinstance(lit_verified, dict):
                plan_data["_literature_metadata"] = lit_verified.get("metadata", {})

            # Build consolidated verified_field_mappings (Steps 2+3+4 merged)
            verified: list[dict] = []

            # From Step 2: keep non-redundant, non-ambiguous field_mappings
            for fm in gsm_extract.get("field_mappings", []) if isinstance(gsm_extract, dict) else []:
                note = (fm.get("note") or "").lower()
                is_redundant = "redundant" in note
                is_ambiguous = "ambiguous" in note or "uncertain" in note
                if not is_redundant and not is_ambiguous:
                    verified.append({**fm, "_src": "step2_gsm"})

            # From Step 4 verified_items (per-item ORC verification with GSM context)
            verified_items = lit_verified.get("verified_items", []) if isinstance(lit_verified, dict) else []
            for vi in verified_items:
                if isinstance(vi, dict) and vi.get("confidence", 0) >= 0.7 and vi.get("resolved"):
                    verified.append({
                        "raw_field": vi["raw_value"],
                        "canonical": vi["resolved"],
                        "gsm_id": vi.get("gsm_id", ""),
                        "source_field": vi.get("source_field", ""),
                        "confidence": vi.get("confidence", 0),
                        "source": vi.get("verified_by", "literature"),
                        "_src": f"step4_orc_{vi.get('source_step', '')}"
                    })

            # Fallback: old-style Step 4 abbreviation resolution
            verified_abbrs = lit_verified.get("abbreviations", {}) if isinstance(lit_verified, dict) else {}
            for abbr in (gsm_extract.get("ambiguous_abbreviations", [])
                         if isinstance(gsm_extract, dict) else []):
                raw = abbr.get("raw_value", "")
                resolved = verified_abbrs.get(raw)
                if resolved and (raw, resolved) not in _seen_keys:
                    verified.append({
                        "raw_field": raw, "canonical": resolved,
                        "gsm_id": abbr.get("gsm_id", ""),
                        "source": "literature_abbr", "_src": "step4_literature_abbr"
                    })

            # From Step 3: high-confidence filename extractions (deduplicated)
            _seen_keys = {(fm.get("raw_field", ""), fm.get("canonical", "")) for fm in verified}
            _skip_keys = {"gsm_id", "role", "file_type", "format", "extension"}
            for fe in file_extract.get("filename_extractions", []) if isinstance(file_extract, dict) else []:
                conf = fe.get("confidence", 0)
                if isinstance(conf, (int, float)) and conf >= 0.7:
                    for k, v in fe.get("components", {}).items():
                        if v and k not in _skip_keys and (k, k) not in _seen_keys:
                            verified.append({
                                "raw_field": k, "canonical": k,
                                "confidence": conf, "source": "filename",
                                "_src": "step3_filename"
                            })
                            _seen_keys.add((k, k))

            # From Step 4: all literature metadata fields
            for k, v in (lit_verified.get("metadata", {}) if isinstance(lit_verified, dict) else {}).items():
                if not k.startswith("_") and v:
                    verified.append({
                        "raw_field": k, "canonical": k,
                        "source": "literature", "_src": "step4_literature"
                    })

            plan_data["_verified_field_mappings"] = verified

            plan = ProcessingPlan.from_llm_response(
                report.gse_id, json.dumps(plan_data),
            )

            plan = self._validate_loop(plan, report, "multi_step")
            if plan and plan.is_valid():
                self._matcher.store_strategy(report, plan.to_json(), success=True)

            if plan and (plan.requires_literature_check or report.gse_info.pubmed_id or report.gse_info.title):
                plan = self._enrich_with_literature(plan, report)

            return plan

        except Exception as exc:
            logger.warning("Multi-step extraction failed, falling back to single prompt: %s", exc)
            return self._plan_single_prompt(report)

    def _plan_single_prompt(self, report: GseReport) -> ProcessingPlan | None:
        """Fallback: single monolithic prompt (original behavior)."""
        plan = self._generator.generate(report)
        if plan is None:
            return None

        plan = self._validate_loop(plan, report, "initial")
        if plan is None:
            return None

        if plan.requires_literature_check \
           or (report.gse_info.pubmed_id or report.gse_info.title):
            plan = self._enrich_with_literature(plan, report)

        return plan

    def _validate_loop(
        self, plan: ProcessingPlan, report: GseReport, stage: str = ""
    ) -> ProcessingPlan | None:
        """Validate and self-correct a plan."""
        for round_idx in range(_MAX_VALIDATION_ROUNDS):
            issues = self._validator.validate(plan, report)
            if not issues:
                if round_idx == 0 and stage == "initial":
                    self._matcher.store_strategy(report, plan.to_json(), success=True)
                logger.info("Plan validated successfully (%s, round %d)", stage, round_idx + 1)
                return plan

            logger.warning("Plan validation issues (%s, round %d): %s", stage, round_idx + 1, issues)
            revised = self._validator.validate_with_llm(plan, report, issues)
            if revised is None:
                logger.error("LLM revision failed on %s round %d", stage, round_idx + 1)
                return plan

            if revised.to_json() == plan.to_json():
                logger.info("No changes in %s revision round %d", stage, round_idx + 1)
                return plan

            plan = revised

        logger.warning("Max validation rounds reached, returning best-effort plan")
        return plan

    def _enrich_with_literature(
        self, plan: ProcessingPlan, report: GseReport,
    ) -> ProcessingPlan:
        """Fetch PubMed literature and use it to enrich the plan.

        If a pubmed_id exists, fetches directly. Otherwise searches by title.
        Uses LLM to extract structured experimental metadata from the abstract,
        then asks LLM to revise the plan with the enriched context.
        """
        enrichment = self._fetch_literature_enrichment(report)
        if enrichment is None:
            return plan

        # Store enrichment on plan for executor injection
        plan._literature_metadata.update({k: v for k, v in enrichment.items()
                                          if not k.startswith("_")})

        logger.info("Literature enrichment: %s", list(enrichment.keys())[:5])

        revised = self._revise_plan_with_literature(plan, report, enrichment)
        if revised is None:
            return plan
        # Carry literature metadata to revised plan
        revised._literature_metadata = plan._literature_metadata
        return self._validate_loop(revised, report, "literature_enriched") or revised

    def _fetch_literature_enrichment(self, report: GseReport) -> dict | None:
        """Fetch PubMed record and LLM-extract structured metadata."""
        try:
            from scruticell.literature.extractor import extract_metadata_from_text
            from scruticell.literature.pubmed import fetch_pubmed_record, search_pubmed

            pubmed_id = report.gse_info.pubmed_id
            if not pubmed_id and report.gse_info.title:
                ids = search_pubmed(report.gse_info.title, retmax=1)
                if not ids:
                    # Full title may have unicode/suffix that breaks PubMed search.
                    # Fallback: strip brackets, truncate at last complete word.
                    import re
                    clean = re.sub(r'\[.*?\]|\(.*?\)', '', report.gse_info.title)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    # Truncate to 80 chars at last word boundary
                    truncated = clean[:80].rsplit(' ', 1)[0] if len(clean) > 80 else clean
                    ids = search_pubmed(truncated, retmax=3)
                if ids:
                    pubmed_id = ids[0]
                    logger.info("Found PubMed ID by title search: %s", pubmed_id)
                else:
                    logger.warning("No PubMed record found for title: %s", report.gse_info.title[:80])
                    return None

            if not pubmed_id:
                return None

            record = fetch_pubmed_record(pubmed_id)
            if not record or not record.get("abstract"):
                logger.warning("No abstract found for PubMed ID %s", pubmed_id)
                return None

            text = f"Title: {record.get('title', '')}\n\nAbstract: {record.get('abstract', '')}"
            metadata = extract_metadata_from_text(text, use_llm=True, llm_model=None)
            if metadata:
                metadata["_pubmed_id"] = pubmed_id
                metadata["_journal"] = record.get("journal", "")
                metadata["_year"] = record.get("year", "")
                logger.info("LLM extracted %d metadata fields from literature", len(metadata))

            is_sparse = not metadata or len(metadata) < 3
            if is_sparse and pubmed_id:
                logger.info("Abstract extraction sparse (%d fields), trying PDF RAG...",
                            len(metadata) if metadata else 0)
                try:
                    from scruticell.literature.pdf_agent import PDFAgent
                    agent = PDFAgent()
                    gaps = {
                        "fields": [
                            "sequencing_platform", "normalization_method",
                            "n_batches", "n_cells", "doublet_removal",
                            "reference_genome", "alignment_tool",
                        ],
                    }
                    pdf_result = agent.extract(pubmed_id=pubmed_id, gaps=gaps)
                    if pdf_result and pdf_result.get("metadata"):
                        pdf_metadata = pdf_result["metadata"]
                        if not metadata:
                            metadata = {}
                        for k, v in pdf_metadata.items():
                            if k not in metadata:
                                metadata[k] = v
                        metadata["_source"] = "pdf_rag"
                        logger.info("PDF RAG enriched with %d additional fields",
                                    len(pdf_metadata))
                except Exception as exc:
                    logger.warning("PDF RAG enrichment failed: %s", exc)

            return metadata

        except Exception as exc:
            logger.warning("Literature enrichment failed: %s", exc)
            return None

    def _revise_plan_with_literature(
        self, plan: ProcessingPlan, report: GseReport, enrichment: dict,
    ) -> ProcessingPlan | None:
        """Ask LLM to revise the plan using literature-enriched context."""
        try:
            from langchain_openai import ChatOpenAI

            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

            literature_context = json.dumps(enrichment, indent=2, ensure_ascii=False)

            prompt = f"""You previously generated this processing plan:

PLAN:
{plan.to_json()}

The GSE dataset has an associated publication. I've extracted structured
experimental metadata from its abstract:

LITERATURE METADATA:
{literature_context}

ORIGINAL REPORT:
{report.to_json()}

Please revise the plan using the literature-derived metadata to:
1. CORRECT any misinterpretations of GSM metadata
2. RESOLVE ambiguous abbreviations (e.g., "E3" = APOE3 genotype, "E4" = APOE4)
3. ADD missing metadata fields found in the literature
4. IMPROVE field_mappings and batch descriptions
5. UPDATE strategy_notes and confidence

Return ONLY the revised JSON plan. Keep validated parts unchanged."""

            llm_kwargs: dict[str, Any] = {
                "model": LLM_MODEL,
                "temperature": 0.0,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY

            llm = ChatOpenAI(**llm_kwargs)
            response = llm.invoke(prompt)
            content = str(response.content).strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            return ProcessingPlan.from_llm_response(plan.gse_id, content)
        except Exception as exc:
            logger.warning("LLM literature revision failed: %s", exc)
            return None
