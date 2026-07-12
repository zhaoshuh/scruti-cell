"""Metadata Extractor sub-agent: flowchart + extract_metadata() code generation.

Reads compact extraction context from SOFT data, generates:
  A. experiment_flowchart (Mermaid)
  B. extract_metadata() Python function

Sandbox execution happens in inspect_tool.py after code generation.
"""

from __future__ import annotations

from scruticell.preprocessing.prompts import METADATA_EXTRACTION_PROMPT


def create_metadata_extractor_subagent(model: str = "deepseek-chat"):
    """Create the Metadata Extractor sub-agent.

    Input: compact three-layer extraction context (gse_global + gse_protocols + per_gsm_text_fields)
    Output: {"experiment_flowchart": str, "extraction_code": str}
    """
    from deepagents import CompiledSubAgent

    return CompiledSubAgent(
        name="metadata-extractor",
        description=(
            "Extract experiment flowchart and generate Python extraction code "
            "from compact SOFT metadata. Reads a deduplicated field-value→GSM mapping "
            "and produces: (1) a Mermaid flowchart of the experimental design, "
            "(2) an extract_metadata() Python function for per-GSM structured metadata extraction. "
            "Use this to enrich field_value_maps before classification."
        ),
        tools=[],
        system_prompt=METADATA_EXTRACTION_PROMPT,
        model=model,
    )
