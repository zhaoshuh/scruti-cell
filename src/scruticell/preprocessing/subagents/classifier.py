"""Classifier sub-agent: classifies sample groups and modalities.

Receives compact FileContextReport, returns L14 compact classification output.
Modality definitions provided via deepagents skills ('modality_knowledge').
"""

from __future__ import annotations

from scruticell.preprocessing.prompts import CLASSIFIER_SYSTEM_PROMPT


def create_classifier_subagent(model=None):
    """Create the Classifier sub-agent with compact input/output.

    Input: Compact FileContextReport (field_value_maps + file_signal_groups)
    Output: Compact classification with GSM-deduplicated groups.
    """
    from scruticell.config import LLM_MODEL
    if model is None:
        model = LLM_MODEL
    from deepagents import CompiledSubAgent

    return CompiledSubAgent(
        name="classifier",
        description=(
            "Classify GEO dataset files into sample groups and modalities "
            "using a compact FileContextReport. Returns GSM-deduplicated output: "
            "multiple GSM sharing the same classification are merged into one group. "
            "Use this sub-agent for efficient, token-optimized classification."
        ),
        tools=[],
        system_prompt=CLASSIFIER_SYSTEM_PROMPT,
        model=model,
    )
