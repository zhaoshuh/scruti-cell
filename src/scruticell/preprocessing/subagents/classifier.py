"""Classifier sub-agent: classifies sample groups and modalities.

Receives compact FileContextReport, returns L14 compact classification output.
"""

from __future__ import annotations

from scruticell.preprocessing.prompts import build_classifier_prompt


def create_classifier_subagent(model: str = "deepseek-chat"):
    """Create the Classifier sub-agent with compact input/output.

    Input: Compact FileContextReport (field_value_maps + file_signal_groups)
    Output (L14): Compact classification with GSM-deduplicated groups.
    Modality definitions loaded from skills/modality_knowledge/SKILL.md at runtime.
    """
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
        system_prompt=build_classifier_prompt(),
        model=model,
    )
