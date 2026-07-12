"""File Prep sub-agent: handles download, decompress, and inspect.

Returns compact FileContextReport with field_value_maps + file_signal_groups.
"""

from __future__ import annotations

from scruticell.preprocessing.prompts import FILE_PREP_SYSTEM_PROMPT
from scruticell.preprocessing.tools import download_gse, decompress_files, inspect_files


def create_file_prep_subagent(model: str = "deepseek-chat"):
    """Create the File Prep sub-agent with compact report output.

    Uses L1/L2/L3 optimized context format: field_value_maps +
    file_signal_groups + file_summary_groups instead of per-entry listings.
    """
    from deepagents import CompiledSubAgent

    return CompiledSubAgent(
        name="file-prep",
        description=(
            "Download, decompress, and inspect all files for a GSE accession. "
            "Returns a COMPACT FileContextReport with deduplicated metadata "
            "(field_value_maps) and grouped file signals with content previews "
            "(file_signal_groups including actual data samples). "
            "Use this sub-agent for efficient context preparation."
        ),
        tools=[download_gse, decompress_files, inspect_files],
        system_prompt=FILE_PREP_SYSTEM_PROMPT,
        model=model,
    )
