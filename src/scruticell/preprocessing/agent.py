"""Main orchestrator agent for omics preprocessing.

Creates a deepagent with compact context passing and minimal middleware.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def create_preprocessing_agent(
    model: str = "deepseek-chat",
    api_key: str | None = None,
    base_url: str | None = None,
    enable_evidence: bool = False,
):
    """Create the main preprocessing orchestrator agent.

    L15: Excludes unnecessary middleware (filesystem tools not needed
    by the orchestrator — sub-agents handle file operations).
    """
    from deepagents import create_deep_agent
    from langchain_openai import ChatOpenAI

    from scruticell.preprocessing.prompts import MAIN_AGENT_SYSTEM_PROMPT
    from scruticell.preprocessing.subagents import (
        create_classifier_subagent,
        create_file_prep_subagent,
    )
    from scruticell.preprocessing.tools import organize_output

    # Build LLM kwargs
    llm_kwargs: dict = {"model": model, "temperature": 0.0}
    if base_url:
        llm_kwargs["openai_api_base"] = base_url
    if api_key:
        llm_kwargs["openai_api_key"] = api_key
    llm = ChatOpenAI(**llm_kwargs)

    # L15: Minimal filesystem — main agent only needs read_file for report review
    from deepagents.backends import StateBackend
    backend = StateBackend()

    subagents: list = [
        create_file_prep_subagent(model=model),
        create_classifier_subagent(model=model),
    ]
    if enable_evidence:
        from scruticell.preprocessing.subagents.evidence import create_evidence_subagent
        subagents.append(create_evidence_subagent(model=model))

    agent = create_deep_agent(
        model=llm,
        tools=[organize_output],
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        subagents=subagents,
        backend=backend,
    )
    return agent


def preprocess_gse(
    gse_id: str,
    output_dir: str | None = None,
    model: str = "deepseek-chat",
    api_key: str | None = None,
    base_url: str | None = None,
    enable_evidence: bool = True,
    verbose: bool = False,
) -> dict:
    """Run the full preprocessing pipeline for a GSE accession."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL
    if api_key is None:
        api_key = LLM_API_KEY
    if base_url is None:
        base_url = LLM_BASE_URL

    output_path = output_dir or str(Path.cwd() / "output" / gse_id)

    agent = create_preprocessing_agent(
        model=model, api_key=api_key, base_url=base_url,
        enable_evidence=enable_evidence,
    )

    task_instruction = (
        f"Preprocess GEO dataset {gse_id}. "
        f"Download, decompress, inspect files, classify modalities, "
        f"and organize output to: {output_path}. "
        f"Use the compact pipeline — pass context directly, be concise."
    )

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task_instruction}]}
        )
        messages = result.get("messages", [])
        final_message = ""
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content:
                final_message = str(msg.content)
                break

        try:
            parsed = json.loads(final_message)
        except json.JSONDecodeError:
            parsed = {
                "gse_id": gse_id, "output_dir": output_path,
                "sample_groups": [], "unclassified": [],
                "evidence_used": enable_evidence,
                "errors": ["Failed to parse agent output as JSON"],
                "raw_output": final_message[:2000],
            }
        return parsed

    except Exception as e:
        logger.exception("Preprocessing failed for %s", gse_id)
        return {
            "gse_id": gse_id, "output_dir": output_path,
            "sample_groups": [], "unclassified": [],
            "evidence_used": enable_evidence,
            "errors": [str(e)],
        }
