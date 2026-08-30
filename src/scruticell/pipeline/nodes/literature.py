"""Literature node: fetches PubMed metadata to supplement GEO metadata."""

from __future__ import annotations

import logging
from typing import Any

from scruticell.literature.extractor import extract_metadata_from_text
from scruticell.literature.pubmed import fetch_pubmed_record, search_pubmed
from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


def literature_node(state: PipelineState) -> dict[str, Any]:
    """Search PubMed for associated publication and extract relevant metadata.

    Uses the pubmed_id from SOFT metadata (or searches by title/description)
    to fetch the paper's abstract and methods section. Extracts:
    - Sample preparation details
    - Sequencing platform and parameters
    - Preprocessing/normalization methods
    - Experimental design (batches, replicates)
    - Any metadata not captured in SOFT fields

    Args:
        state: Pipeline state with pubmed_id and raw_metadata set.

    Returns:
        Partial state update with literature_context, methods_text,
        literature_metadata_supplement.
    """
    logger.info("Searching literature for %s", state.gse_id)

    pubmed_id = state.pubmed_id
    raw_metadata = state.raw_metadata
    literature_context: dict[str, Any] = {}
    methods_text = ""
    supplement: dict[str, Any] = {}

    if not pubmed_id:
        pubmed_id = _search_by_title(state, raw_metadata)

    if pubmed_id:
        logger.info("Fetching PubMed record: %s", pubmed_id)
        try:
            record = fetch_pubmed_record(pubmed_id)
            literature_context = record

            abstract = record.get("abstract", "")
            if abstract:
                text = abstract
            else:
                text = ""
            methods_text = text

            if text:
                supplement = extract_metadata_from_text(
                    text, use_llm=False
                )
                logger.info(
                    "Extracted %d metadata fields from literature: %s",
                    len(supplement), list(supplement.keys()),
                )

                if not supplement or len(supplement) < 3:
                    logger.info("Regex extraction sparse, trying PDF RAG for %s", state.gse_id)
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
                            if not supplement:
                                supplement = {}
                            for k, v in pdf_result["metadata"].items():
                                if k not in supplement:
                                    supplement[k] = v
                            logger.info("PDF RAG supplemented with %d fields",
                                        len(pdf_result["metadata"]))
                    except Exception as exc:
                        logger.warning("PDF RAG failed for %s: %s", state.gse_id, exc)
        except Exception as exc:
            logger.warning("Failed to fetch PubMed record %s: %s", pubmed_id, exc)

    if not pubmed_id:
        logger.info("No PubMed ID found for %s", state.gse_id)

    return {
        "literature_context": literature_context,
        "methods_text": methods_text,
        "literature_metadata_supplement": supplement,
    }


def _search_by_title(
    state: PipelineState, raw_metadata: dict[str, Any]
) -> str:
    """Try to find a PubMed ID by searching the dataset title."""
    title = raw_metadata.get("title", state.gse_id)
    if not title or title == state.gse_id:
        return ""

    try:
        pmids = search_pubmed(str(title), retmax=3)
        if pmids:
            logger.info("Found %d PubMed IDs by title search", len(pmids))
            return pmids[0]
    except Exception as exc:
        logger.warning("PubMed title search failed: %s", exc)

    return ""
