"""Evidence gathering sub-agent: searches PubMed and reads papers.

Triggers when the classifier is uncertain about a dataset's modality.
Searches PubMed, reads abstracts, and downloads PDFs for RAG-based
experimental methods extraction.
"""

from __future__ import annotations


def create_evidence_subagent(model: str = "deepseek-chat"):
    """Create the Evidence Gathering sub-agent.

    Uses literature/pubmed.py and literature/pdf_agent.py for
    paper search and RAG-based methods extraction.

    Args:
        model: LLM model identifier

    Returns:
        A CompiledSubAgent for evidence gathering.
    """
    from deepagents import CompiledSubAgent
    from scruticell.preprocessing.evidence.tools import (
        read_paper_methods,
        search_gse_papers,
    )

    return CompiledSubAgent(
        name="evidence",
        description=(
            "Gather evidence from PubMed papers to resolve modality ambiguities. "
            "Searches for the GSE related publications using PubMed, reads abstracts, "
            "and downloads full-text PDFs for RAG-based methods extraction when needed. "
            "Use this when the metadata signals are contradictory or ambiguous."
        ),
        tools=[search_gse_papers, read_paper_methods],
        system_prompt=(
            "You are a scientific literature research specialist. "
            "Your job: search PubMed and read papers to determine the true "
            "experimental design of a GEO dataset when metadata is ambiguous.\n\n"
            "## Workflow\n"
            "1. Call `search_gse_papers(gse_id, organism, keywords)` to find related papers.\n"
            "2. Review the paper titles and abstracts.\n"
            "3. If an open-access paper (has `pmcid`) likely describes the experiment, "
            "call `read_paper_methods(pmid, gsm_ambiguities)` to extract methods.\n"
            "4. Synthesize findings into a structured EvidenceReport.\n\n"
            "## Output Format\n"
            "Return ONLY:\n"
            '{"source": "pubmed_pdf"|"pubmed_abstract"|"none", '
            '"pmids": [...], '
            '"findings": "What experiments this GSE actually contains", '
            '"excerpts": ["Direct quotes from the paper"], '
            '"confidence_impact": "How this resolves the ambiguity"}'
        ),
        model=model,
    )
