"""Main orchestrator agent for omics preprocessing.

Creates a deepagent with compact context passing, rubric-based quality gating,
and minimal middleware.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def verify_classification_tool(classification_json_str: str) -> str:
    """Verify classifier output JSON for structural correctness.

    Call this with the raw JSON string from the classifier sub-agent's output.
    Returns a JSON report with passed, checks, and a failure_summary.
    """
    from scruticell.preprocessing.verification import verify_classification
    try:
        data = json.loads(classification_json_str)
    except json.JSONDecodeError as e:
        return json.dumps({"passed": False, "error": f"Invalid JSON: {e}"})
    verif = verify_classification(data)
    return json.dumps(verif.model_dump(), indent=2, ensure_ascii=False)


def create_preprocessing_agent(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    enable_evidence: bool = False,
):
    """Create the main preprocessing orchestrator agent.

    L15: Excludes unnecessary middleware (filesystem tools not needed
    by the orchestrator — sub-agents handle file operations).
    """
    from deepagents import create_deep_agent
    from deepagents.middleware.rubric import RubricMiddleware
    from langchain_openai import ChatOpenAI

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    from scruticell.preprocessing.prompts import MAIN_AGENT_SYSTEM_PROMPT, CLASSIFIER_RUBRIC
    from scruticell.preprocessing.subagents import (
        create_classifier_subagent,
        create_file_prep_subagent,
    )
    from scruticell.preprocessing.tools import organize_output

    if model is None:
        model = LLM_MODEL
    if api_key is None:
        api_key = LLM_API_KEY
    if base_url is None:
        base_url = LLM_BASE_URL

    # Build LLM kwargs
    llm_kwargs: dict = {"model": model, "temperature": 0.0}
    if base_url:
        llm_kwargs["openai_api_base"] = base_url
    if api_key:
        llm_kwargs["openai_api_key"] = api_key
    llm = ChatOpenAI(**llm_kwargs)

    # L15: Minimal filesystem — main agent only needs read_file for report review
    from deepagents.backends import StateBackend
    from langgraph.checkpoint.memory import MemorySaver

    backend = StateBackend()
    checkpointer = MemorySaver()

    subagents: list = [
        create_file_prep_subagent(model=llm),
        create_classifier_subagent(model=llm),
    ]
    if enable_evidence:
        from scruticell.preprocessing.subagents.evidence import create_evidence_subagent
        subagents.append(create_evidence_subagent(model=llm))

    agent = create_deep_agent(
        model=llm,
        tools=[organize_output],
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        subagents=subagents,
        backend=backend,
        skills=["modality_knowledge", "file_reader"],
        interrupt_on={"classifier": True},
        checkpointer=checkpointer,
        middleware=[
            RubricMiddleware(
                model=llm,
                tools=[verify_classification_tool],
                max_iterations=3,
            ),
        ],
    )
    return agent


def preprocess_gse(
    gse_id: str,
    output_dir: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    enable_evidence: bool = True,
    verbose: bool = False,
) -> dict:
    """Run the full preprocessing pipeline for a GSE accession."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, get_output_path
    from scruticell.preprocessing.prompts import CLASSIFIER_RUBRIC
    if model is None:
        model = LLM_MODEL
    if api_key is None:
        api_key = LLM_API_KEY
    if base_url is None:
        base_url = LLM_BASE_URL

    output_path = output_dir or str(get_output_path(gse_id))

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
            {
                "messages": [{"role": "user", "content": task_instruction}],
                "rubric": CLASSIFIER_RUBRIC,
            },
            config={"configurable": {"thread_id": gse_id}},
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
                "verification_report": None,
                "repair_attempts": 0,
                "fallback": False,
            }
            return parsed

        # Verify classification output
        try:
            from scruticell.preprocessing.verification import verify_classification
            verif = verify_classification(parsed)
            parsed["verification_report"] = verif.model_dump() if verif else None
            parsed["repair_attempts"] = verif.repair_attempts if verif else 0
            parsed["fallback"] = verif.fallback if verif else False
        except Exception as e:
            logger.warning("Classification verification failed: %s", e)
            parsed["verification_report"] = None
            parsed["repair_attempts"] = 0
            parsed["fallback"] = False

        return parsed

    except Exception as e:
        logger.exception("Preprocessing failed for %s", gse_id)
        return {
            "gse_id": gse_id, "output_dir": output_path,
            "sample_groups": [], "unclassified": [],
            "evidence_used": enable_evidence,
            "errors": [str(e)],
        }
