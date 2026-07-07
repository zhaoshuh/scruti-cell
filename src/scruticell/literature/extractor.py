"""Metadata extraction from scientific literature text.

Extracts structured experimental metadata from paper abstracts and
methods sections. Uses a two-tier approach:
1. Rule-based regex patterns for common metadata fields
2. LLM-based extraction for complex/unstructured descriptions (optional)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from scruticell.utils.tokens import invoke_and_track

logger = logging.getLogger(__name__)

_PLATFORM_PATTERNS = [
    (re.compile(r"10[xX]\s*Genomics\s*(?:Chromium\s*)?(?:3'|5')?\s*(?:v|version)?\s*(\d+)", re.I), "10X_Genomics_v{0}"),
    (re.compile(r"Chromium\s*(?:Single\s*Cell\s*)?(?:3'|5')?\s*(?:v|version)?\s*(\d+)", re.I), "10X_Chromium_v{0}"),
    (re.compile(r"SMART-seq\s*(\d*)", re.I), None),
    (re.compile(r"Drop-seq", re.I), "Drop_seq"),
    (re.compile(r"inDrop", re.I), "inDrop"),
    (re.compile(r"Seq-Well", re.I), "Seq_Well"),
    (re.compile(r"SPLiT-seq", re.I), "SPLiT_seq"),
    (re.compile(r"Parse\s*Biosciences", re.I), "Parse_Biosciences"),
    (re.compile(r"DNBelab\s*C4", re.I), "DNBelab_C4"),
    (re.compile(r"Illumina\s*(?:NovaSeq|HiSeq|NextSeq|MiSeq)\s*(\S*)", re.I), None),
]

_BATCH_PATTERNS = [
    re.compile(r"(?:processed|sequenced|prepared)\s*(?:in|across)\s*(\d+)\s*(?:independent\s*)?batches?", re.I),
    re.compile(r"(\d+)\s*(?:independent\s*)?batches?\s*(?:of|were|was)", re.I),
    re.compile(r"batch(?:es)?\s*(?:was|were)?\s*(\d+)", re.I),
    re.compile(r"lane(?:s)?\s*(\d+)", re.I),
]

_NORMALIZATION_PATTERNS = [
    (re.compile(r"log[\s_-]?normaliz(?:ed|ation)", re.I), "log_normalized"),
    (re.compile(r"TPM", re.I), "TPM"),
    (re.compile(r"FPKM", re.I), "FPKM"),
    (re.compile(r"RPKM", re.I), "RPKM"),
    (re.compile(r"CPM", re.I), "CPM"),
    (re.compile(r"SCTransform", re.I), "SCTransform"),
    (re.compile(r"sctransform", re.I), "SCTransform"),
    (re.compile(r"raw\s*counts?", re.I), "raw_counts"),
    (re.compile(r"UMI\s*counts?", re.I), "UMI_counts"),
    (re.compile(r"counts?\s*per\s*million", re.I), "CPM"),
    (re.compile(r"library[\s_-]?size\s*normaliz(?:ed|ation)", re.I), "library_size_normalized"),
]

_CELL_COUNT_PATTERNS = [
    re.compile(r"(\d[\d,]*)\s*(?:high[\s_-]?quality\s*)?cells?", re.I),
    re.compile(r"(?:profiled|sequenced|obtained|captured)\s*(\d[\d,]*)\s*cells?", re.I),
    re.compile(r"(\d[\d,]*)\s*single\s*cells?", re.I),
    re.compile(r"dataset\s*.*?(\d[\d,]*)\s*cells?", re.I),
]

_DOUBLET_PATTERNS = [
    re.compile(r"(?:removed|filtered|detected).*?(?:doublets?|multiplets?)", re.I),
    re.compile(r"(?:doublets?|multiplets?).*?(?:removed|filtered|detected|identified)", re.I),
    re.compile(r"DoubletFinder|Scrublet|Solo|scDblFinder|doublet\s*detection", re.I),
]

_MITO_PATTERNS = [
    re.compile(r"mitochondrial\s*(?:genes?|fraction|content|percentage|ratio).*?[<>]=?\s*(\d+)%", re.I),
    re.compile(r"cells?\s*(?:with|having|containing).*?[<>]=?\s*(\d+)%\s*(?:mitochondrial|MT)", re.I),
    re.compile(r"(?:filtered|removed|excluded).*?mitochondrial.*?[<>]=?\s*(\d+)%", re.I),
]

_ORGANISM_PATTERNS = [
    (re.compile(r"Homo\s*sapiens", re.I), "Homo sapiens"),
    (re.compile(r"Mus\s*musculus", re.I), "Mus musculus"),
    (re.compile(r"human\s*(?:samples?|donors?|patients?|subjects?)", re.I), "Homo sapiens"),
    (re.compile(r"mouse|mice|murine", re.I), "Mus musculus"),
    (re.compile(r"Rattus\s*norvegicus", re.I), "Rattus norvegicus"),
    (re.compile(r"Danio\s*rerio", re.I), "Danio rerio"),
    (re.compile(r"Drosophila\s*melanogaster", re.I), "Drosophila melanogaster"),
]

_DISEASE_PATTERNS = [
    re.compile(r"(?:patients?|subjects?|donors?|individuals?)\s*(?:with|diagnosed|suffering)\s*(?:from|with)?\s*([\w\s\-']+)", re.I),
    re.compile(r"healthy\s*(?:control|donor|individual|subject)s?", re.I),
    re.compile(r"(?:COVID[\s-]?19|SARS[\s-]CoV[\s-]2|covid[\s-]?19)", re.I),
    re.compile(r"tumor|tumour|cancer|carcinoma|malignan", re.I),
]

_TISSUE_PATTERNS = [
    re.compile(r"(?:from|in|of)\s+(?:the\s+)?(PBMC|peripheral\s*blood|liver|lung|kidney|brain|heart|spleen|bone\s*marrow|skin|pancreas|colon|intestine|stomach)", re.I),
    re.compile(r"(PBMC|peripheral\s*blood\s*mononuclear\s*cells?)", re.I),
    re.compile(r"(\w+)\s*(?:tissue|biopsy|sample|organ)", re.I),
]

_ALIGNMENT_TOOLS = [
    re.compile(r"(?:aligned|mapped|pseudoaligned|quantified)\s*(?:using|with|by)\s*(\S+)", re.I),
    re.compile(r"(STAR|Cell\s*Ranger|Kallisto|Salmon|STARsolo|Alevin|alevin|BUSTools)", re.I),
]

_GENOME_PATTERNS = [
    re.compile(r"(?:GRCh|hg)\s*(\d+)", re.I),
    re.compile(r"(?:GRCm|mm)\s*(\d+)", re.I),
    re.compile(r"(?:Ensembl|GENCODE)\s*(?:v|version)?\s*(\d+)", re.I),
]


def extract_metadata_from_text(
    text: str,
    use_llm: bool = False,
    llm_model: Optional[str] = None,
) -> dict[str, Any]:
    """Extract structured metadata from paper text (abstract + methods).

    Args:
        text: Paper text to analyze.
        use_llm: If True, supplement regex extraction with LLM analysis.
        llm_model: LLM model to use (requires API key in environment).

    Returns:
        Dict with extracted metadata fields.
    """
    result: dict[str, Any] = _regex_extract_all(text)

    if use_llm and llm_model:
        try:
            llm_result = _llm_extract(text, llm_model)
            _merge_llm_results(result, llm_result)
        except Exception as exc:
            logger.warning("LLM extraction failed: %s", exc)

    return result


def _regex_extract_all(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    platforms = _extract_platforms(text)
    if platforms:
        result["sequencing_platform"] = platforms

    n_batches = _extract_batch_count(text)
    if n_batches is not None:
        result["n_batches_described"] = n_batches

    norm = _extract_normalization(text)
    if norm:
        result["normalization_method"] = norm

    n_cells = _extract_cell_count(text)
    if n_cells:
        result["n_cells_described"] = n_cells

    doublet = _extract_doublet_removal(text)
    if doublet:
        result["doublet_removal"] = True
        result["doublet_tool"] = doublet.get("tool", "")

    mito = _extract_mito_threshold(text)
    if mito:
        result["mito_threshold"] = mito

    organism = _extract_organism(text)
    if organism:
        result["organism_from_text"] = organism

    disease = _extract_disease(text)
    if disease:
        result["disease_context"] = disease

    tissue = _extract_tissue(text)
    if tissue:
        result["tissue_context"] = tissue

    alignment = _extract_alignment(text)
    if alignment:
        result["alignment_tool"] = alignment

    genome = _extract_genome(text)
    if genome:
        result["reference_genome"] = genome

    return result


def _extract_platforms(text: str) -> list[str]:
    platforms = []
    for pattern, templ in _PLATFORM_PATTERNS:
        matches = pattern.findall(text)
        for m in matches:
            if templ:
                if isinstance(m, tuple):
                    m = m[0]
                platforms.append(templ.format(m) if "{" in templ else templ)
            else:
                val = m if isinstance(m, str) else " ".join(str(x) for x in m if x)
                platforms.append(val.strip())
    return sorted(set(platforms))


def _extract_batch_count(text: str) -> Optional[int]:
    for pattern in _BATCH_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def _extract_normalization(text: str) -> list[str]:
    methods = []
    for pattern, label in _NORMALIZATION_PATTERNS:
        if pattern.search(text):
            methods.append(label)
    return sorted(set(methods))


def _extract_cell_count(text: str) -> Optional[int]:
    for pattern in _CELL_COUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            num_str = m.group(1).replace(",", "")
            try:
                n = int(num_str)
                if 100 <= n <= 50_000_000:
                    return n
            except ValueError:
                pass
    return None


def _extract_doublet_removal(text: str) -> Optional[dict[str, str]]:
    for pattern in _DOUBLET_PATTERNS:
        m = pattern.search(text)
        if m:
            matched = m.group(0)
            tool = ""
            for t in ["DoubletFinder", "Scrublet", "Solo", "scDblFinder"]:
                if t.lower() in matched.lower():
                    tool = t
                    break
            return {"matched_text": matched, "tool": tool}
    return None


def _extract_mito_threshold(text: str) -> Optional[int]:
    for pattern in _MITO_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def _extract_organism(text: str) -> Optional[str]:
    for pattern, species in _ORGANISM_PATTERNS:
        if pattern.search(text):
            return species
    return None


def _extract_disease(text: str) -> list[str]:
    diseases = []
    for pattern in _DISEASE_PATTERNS:
        matches = pattern.findall(text)
        for m in matches:
            val = m if isinstance(m, str) else str(m)
            val = val.strip()
            if val and len(val) < 80:
                diseases.append(val)
    return sorted(set(diseases))[:5]


def _extract_tissue(text: str) -> list[str]:
    tissues = []
    for pattern in _TISSUE_PATTERNS:
        matches = pattern.findall(text)
        for m in matches:
            val = m if isinstance(m, str) else str(m)
            val = val.strip().lower()
            if val and val not in ("the", "a", "an", "with", "from", "using"):
                tissues.append(val)
    return sorted(set(tissues))[:8]


def _extract_alignment(text: str) -> Optional[str]:
    for pattern in _ALIGNMENT_TOOLS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return None


def _extract_genome(text: str) -> Optional[str]:
    for pattern in _GENOME_PATTERNS:
        m = pattern.search(text)
        if m:
            prefix = ""
            if "GRCh" in m.group(0) or "hg" in m.group(0).lower():
                prefix = "GRCh"
            elif "GRCm" in m.group(0) or "mm" in m.group(0).lower():
                prefix = "GRCm"
            return f"{prefix}{m.group(1)}"
    return None


def _llm_extract(text: str, model: str) -> dict[str, Any]:
    """Use LLM to extract structured metadata from paper text.

    Requires DEEPSEEK_API_KEY or OPENAI_API_KEY environment variable.
    """
    prompt = _build_extraction_prompt(text)
    try:
        from langchain_openai import ChatOpenAI
        from scruticell.config import LLM_API_KEY, LLM_BASE_URL

        llm_kwargs: dict[str, Any] = {"model": model, "temperature": 0}
        if LLM_BASE_URL:
            llm_kwargs["openai_api_base"] = LLM_BASE_URL
        if LLM_API_KEY:
            llm_kwargs["openai_api_key"] = LLM_API_KEY
        llm = ChatOpenAI(**llm_kwargs)
        response = invoke_and_track(llm, prompt, "literature_extract")
        return _parse_llm_json(str(response.content))
    except ImportError:
        logger.warning("langchain_openai not installed, skipping LLM extraction")
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
    return {}


def _build_extraction_prompt(text: str) -> str:
    return f"""Extract the following metadata from this scientific paper text.
Return ONLY a valid JSON object. Use null for missing values.

Fields to extract:
- sequencing_platform: e.g. "10X Genomics v3", "SMART-seq2", "Drop-seq"
- n_batches: integer, number of experimental batches described
- normalization_method: e.g. "log_normalized", "SCTransform", "raw_counts", "TPM"
- n_cells: integer, total cells in the dataset
- doublet_removal: boolean, whether doublets were removed
- doublet_tool: tool name if mentioned
- mito_threshold: integer percentage for mitochondrial filtering
- organism: species name
- sample_tissues: array of tissue names studied
- disease_condition: the disease/condition studied
- alignment_tool: e.g. "STAR", "Cell Ranger", "Kallisto"
- reference_genome: genome build used
- cell_types: array of cell types mentioned

Paper text:
{text[:8000]}
"""
    return prompt


def _parse_llm_json(content: str) -> dict[str, Any]:
    import json
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def _merge_llm_results(base: dict, llm: dict) -> None:
    for key, value in llm.items():
        if value is not None and value != "" and value != []:
            if key not in base or not base[key]:
                base[key] = value
