"""L2+L12: Groups per-file signals by deduplicated fingerprint.

Reduces 150 identical per-file signal entries to ~3 representative groups.
L12: Each group preserves GSM→file linkage via gsm_ids and ancestry_pattern.
"""

from __future__ import annotations

import re
from collections import defaultdict

from scruticell.preprocessing.models import FileSignalGroup, FileSignals


def _extract_ancestry_pattern(ancestries: list[str]) -> str:
    """Extract a template from a list of ancestry paths.

    "GSM001/barcodes.tsv.gz", "GSM002/barcodes.tsv.gz"
    → "{GSM}/barcodes.tsv.gz"
    """
    if not ancestries:
        return ""
    if len(ancestries) == 1:
        return ancestries[0]

    parts = [a.replace("\\", "/").split("/") for a in ancestries]
    if not all(len(p) == len(parts[0]) for p in parts):
        return ancestries[0]

    template_parts: list[str] = []
    for idx in range(len(parts[0])):
        values = {p[idx] for p in parts}
        if len(values) == 1:
            template_parts.append(values.pop())
        else:
            template_parts.append("{GSM}")

    return "/".join(template_parts)


def _extract_gsm_ids(ancestry: str) -> list[str]:
    """Extract GSM IDs from an ancestry path."""
    return re.findall(r"GSM\d+", ancestry)


def group_file_signals(
    per_file_signals: list[FileSignals],
) -> list[FileSignalGroup]:
    """Group FileSignals by content fingerprint and filename pattern.

    Args:
        per_file_signals: List of per-file signal objects.

    Returns:
        List of FileSignalGroup, each representing a unique signal pattern.
    """
    groups: dict[str, list[FileSignals]] = defaultdict(list)

    for sig in per_file_signals:
        fp = sig.signal_fingerprint()
        groups[fp].append(sig)

    result: list[FileSignalGroup] = []
    for fp, sigs in sorted(groups.items(), key=lambda x: -len(x[1])):
        ancestries = [s.ancestry for s in sigs]
        all_gsm_ids: list[str] = []
        for a in ancestries:
            all_gsm_ids.extend(_extract_gsm_ids(a))

        # Build per-file ancestry→GSM mapping (no inference, pure extraction)
        per_file_gsm_id: dict[str, str | None] = {}
        for sig in sigs:
            gsm_ids = _extract_gsm_ids(sig.ancestry)
            per_file_gsm_id[sig.ancestry] = gsm_ids[0] if gsm_ids else None

        result.append(FileSignalGroup(
            pattern=sigs[0].filename.raw_name if sigs else fp,
            representative=sigs[0],
            file_count=len(sigs),
            gsm_ids=sorted(set(all_gsm_ids)),
            ancestry_pattern=_extract_ancestry_pattern(ancestries),
            per_file_gsm_id=per_file_gsm_id,
        ))

    return result
