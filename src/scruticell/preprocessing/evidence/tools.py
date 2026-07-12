"""Evidence gathering tools for the preprocessing agent.

Thin wrappers around literature/pubmed.py and literature/pdf_agent.py,
exposed as synchronous callable tools for deepagent sub-agents.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from scruticell.literature.pubmed import fetch_pubmed_record, search_pubmed

logger = logging.getLogger(__name__)


def search_gse_papers(gse_id: str, organism: str = "",
                      keywords: str = "") -> dict:
    """Search PubMed for papers related to a GEO dataset.

    Constructs a targeted query from available metadata and returns
    matching PMIDs with their titles and abstracts.

    Args:
        gse_id: GEO accession ID (e.g. "GSE215353")
        organism: Organism name (e.g. "Homo sapiens")
        keywords: Additional keywords from metadata (e.g. "brain methylation")

    Returns:
        A dict with:
            query: str — the constructed PubMed query
            pmids: list[str] — matching PMIDs
            papers: list[dict] — each with pmid, title, abstract, pmcid, doi
    """
    # Build query
    parts = [gse_id]
    if organism:
        parts.append(organism)
    if keywords:
        parts.append(keywords)
    query = " ".join(parts)

    papers: list[dict] = []
    try:
        pmids = search_pubmed(query, retmax=5)
        for pmid in pmids:
            record = fetch_pubmed_record(pmid)
            papers.append({
                "pmid": record.get("pmid", pmid),
                "title": record.get("title", ""),
                "abstract": record.get("abstract", "")[:2000],
                "pmcid": record.get("pmcid", ""),
                "doi": record.get("doi", ""),
                "journal": record.get("journal", ""),
                "year": record.get("year", ""),
                "is_open_access": bool(record.get("pmcid")),
            })
    except Exception as e:
        logger.warning("PubMed search failed for %s: %s", gse_id, e)

    return {
        "query": query,
        "pmids": pmids,
        "papers": papers,
    }


def read_paper_methods(pmid: str, gsm_ambiguities: str = "") -> dict:
    """Download a paper PDF and use RAG to extract experimental methods.

    Uses the existing PDFAgent (PyMuPDF + sentence-transformers + LLM)
    to read the Methods section and answer what experiments were performed.

    Args:
        pmid: PubMed ID of the paper
        gsm_ambiguities: Description of what's ambiguous in the metadata
                         (e.g. "metadata says Bisulfite-Seq but files look like RNA-seq")

    Returns:
        A dict with:
            source: str — "pubmed_pdf" | "pubmed_abstract" | "none"
            pmid: str
            findings: str — what the paper says about the experimental design
            excerpts: list[str] — direct quotes from the paper
    """
    from scruticell.literature.pdf_agent import PDFAgent

    # Quick abstract check first
    try:
        record = fetch_pubmed_record(pmid)
        abstract = record.get("abstract", "")
        if gsm_ambiguities and abstract:
            # Use LLM to check if abstract alone resolves it
            findings = _check_abstract(abstract, gsm_ambiguities)
            if findings:
                return {
                    "source": "pubmed_abstract",
                    "pmid": pmid,
                    "findings": findings,
                    "excerpts": [abstract[:500]],
                }
    except Exception:
        pass

    # Download PDF and use RAG
    try:
        agent = PDFAgent()
        if agent.has_cache(pmid):
            cached = agent.get_cache(pmid)
            if cached:
                return {
                    "source": "pubmed_pdf",
                    "pmid": pmid,
                    "findings": json.dumps(cached.get("verified_items", [])),
                    "excerpts": [],
                }

        result = agent.extract(pmid)
        if result:
            return {
                "source": "pubmed_pdf",
                "pmid": pmid,
                "findings": json.dumps(result.get("verified_items", [])),
                "excerpts": [],
            }
    except Exception as e:
        logger.warning("PDF RAG failed for PMID %s: %s", pmid, e)

    return {
        "source": "none",
        "pmid": pmid,
        "findings": "",
        "excerpts": [],
    }


def _check_abstract(abstract: str, ambiguity: str) -> str:
    """Quick check if the abstract resolves the ambiguity without full PDF."""
    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    from langchain_openai import ChatOpenAI

    prompt = (
        f"Paper abstract:\n{abstract[:2000]}\n\n"
        f"Question: {ambiguity}\n\n"
        f"Based ONLY on the abstract above, answer in one sentence what "
        f"experiments/technologies were used. If the abstract doesn't say, "
        f"respond with exactly 'NOT_IN_ABSTRACT'."
    )

    try:
        llm = ChatOpenAI(
            model=LLM_MODEL, temperature=0.0,
            openai_api_base=LLM_BASE_URL, openai_api_key=LLM_API_KEY,
        )
        response = llm.invoke(prompt)
        content = str(response.content).strip()
        if content and content != "NOT_IN_ABSTRACT":
            return content
    except Exception:
        pass

    return ""
