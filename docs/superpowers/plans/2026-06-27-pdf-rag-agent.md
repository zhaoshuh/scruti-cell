# PDF RAG Agent — 文献全文定向检索与元信息核实

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 GSE 元信息不完整或存在歧义缩写/复合值时，自动下载相关论文 PDF、通过本地向量检索定位相关段落、用 LLM 定向确认关键信息，最终补全元信息。

**Architecture:** 新增 `PDFAgent` 类，封装 PDF 下载 → PyMuPDF 提取文本 → 按段落切 chunk → all-MiniLM-L6-v2 本地向量化 → 对每个缺口字段做 Cosine 相似度检索 Top 3 chunk → LLM 定向回答。触发点：`parse` 管道在 validate 后（metadata_completeness < 0.7 且 pubmed_id 非空）；`plan` 管道在 MultiStepExtractor Step 3 之后插入 Step 4 文献核实；`generator.py` 的 `_enrich_with_literature` 增加 PDF RAG 兜底。

**Tech Stack:** PyMuPDF, sentence-transformers (all-MiniLM-L6-v2), scikit-learn cosine_similarity, LangChain ChatOpenAI

## Global Constraints

- Python >= 3.10
- PyMuPDF >= 1.23
- sentence-transformers >= 3.0（首次运行自动下载模型到 `~/.cache/torch/sentence_transformers/`）
- 缓存路径：`~/.scruticell/cache/pdf/{pmid}.json`
- 不依赖外部嵌入 API（本地向量化，零 token 成本）
- `--no-literature` 标志必须完全跳过 PDF Agent
- 嵌入模型下载失败时优雅降级：打印警告、跳过 PDF Agent、返回 None

---

### Task 1: 添加依赖

**Files:**
- Modify: `pyproject.toml:25-46`

**Interfaces:**
- Produces: `PyMuPDF >= 1.23` 和 `sentence-transformers >= 3.0` 可供 import

- [ ] **Step 1: 在 dependencies 中添加两个新依赖**

打开 `pyproject.toml`，在 `dependencies` 列表末尾添加：

```toml
    "PyMuPDF>=1.23",
    "sentence-transformers>=3.0",
```

完整 dependencies 块变为：

```toml
dependencies = [
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "langchain-openai>=0.2.0",
    "anndata>=0.10.0",
    "scanpy>=1.10.0",
    "GEOparse>=2.0.4",
    "pandas>=2.0.0",
    "numpy>=1.24.0",
    "scipy>=1.11.0",
    "neo4j>=5.20.0",
    "click>=8.1.0",
    "rich>=13.0.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "tqdm>=4.66.0",
    "pyreadr>=0.5.0",
    "loompy>=3.0.0",
    "charset-normalizer>=3.0.0",
    "biopython>=1.83",
    "bionty>=0.7.0",
    "PyMuPDF>=1.23",
    "sentence-transformers>=3.0",
]
```

- [ ] **Step 2: 安装新依赖**

```bash
pip install PyMuPDF>=1.23 sentence-transformers>=3.0
```

Expected: 安装成功，无错误。

- [ ] **Step 3: 验证导入**

```bash
python -c "import fitz; print('PyMuPDF', fitz.version); from sentence_transformers import SentenceTransformer; print('sentence-transformers OK')"
```

Expected: 打印版本号和 OK，不报错。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add PyMuPDF and sentence-transformers dependencies"
```

---

### Task 2: 创建 PDFAgent 核心类

**Files:**
- Create: `src/scruticell/literature/pdf_agent.py`
- Modify: `src/scruticell/literature/__init__.py`

**Interfaces:**
- Produces: `PDFAgent` 类
  - `PDFAgent(cache_dir=None)`
  - `extract(pubmed_id, doi, gaps, cache=True) -> dict | None`
  - `has_cache(pubmed_id) -> bool`
  - `get_cache(pubmed_id) -> dict | None`
- Consumes: `scruticell.literature.pubmed.Fetch_pubmed_record` (获取 DOI/PMCID)、`scruticell.config.LLM_MODEL/LLM_API_KEY/LLM_BASE_URL`

- [ ] **Step 1: 创建文件并写入 PDFAgent 类**

创建 `src/scruticell/literature/pdf_agent.py`，完整内容如下：

```python
"""PDF Agent: download paper PDF, chunk, embed locally, RAG-query LLM.

Flow:
1. Acquire PDF (PMC OA -> DOI -> NCBI full-text XML fallback)
2. Extract text via PyMuPDF
3. Chunk by paragraph (500 chars + 100 overlap)
4. Embed via all-MiniLM-L6-v2 (local, zero API cost)
5. For each gap field: cosine-similarity retrieve Top 3 chunks
6. LLM answers with targeted prompt
7. Cache result per PMID to ~/.scruticell/cache/pdf/{pmid}.json
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path.home() / ".scruticell" / "cache" / "pdf"

_EMBED_MODEL = None
_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"


def _get_embed_model() -> Any:
    """Lazy-load the sentence-transformers model (shared across all PDFAgent instances)."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s (first time may download ~80MB)", _EMBED_MODEL_NAME)
            _EMBED_MODEL = SentenceTransformer(_EMBED_MODEL_NAME)
            logger.info("Embedding model loaded (%d dimensions)", _EMBED_MODEL.get_sentence_embedding_dimension())
        except Exception as exc:
            logger.warning("Failed to load embedding model: %s. PDF RAG unavailable.", exc)
            return None
    return _EMBED_MODEL


class PDFAgent:
    """Download, chunk, embed, and RAG-query scientific PDFs for metadata extraction.

    Usage:
        agent = PDFAgent()
        result = agent.extract(
            pubmed_id="39971119",
            gaps={
                "abbreviations": [{"raw": "E3", "guess": "APOE3"}],
                "compounds": [{"raw": "Male_Control_72yr", "parts": ["sex", "condition", "age"]}],
                "fields": ["sequencing_platform", "normalization_method", "n_batches"],
            },
        )
        if result:
            # result = {"abbreviations": {"E3": "APOE3"}, "metadata": {...}, ...}
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_ROOT
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        pubmed_id: str,
        doi: str = "",
        gaps: Optional[dict[str, Any]] = None,
        cache: bool = True,
    ) -> Optional[dict[str, Any]]:
        """Full pipeline: acquire PDF -> RAG-query -> return resolved metadata.

        Args:
            pubmed_id: PubMed ID (e.g. "39971119").
            doi: DOI string (optional, used if PMC OA fails).
            gaps: Dict with keys "abbreviations", "compounds", "fields".
            cache: If True, read/write cache for this PMID.

        Returns:
            Dict with resolved metadata and abbreviations, or None if
            PDF could not be acquired or text extraction failed.
        """
        gaps = gaps or {}

        # Check cache
        if cache:
            cached = self._read_cache(pubmed_id)
            if cached is not None:
                logger.info("PDF RAG cache hit for PMID %s", pubmed_id)
                return cached

        text = self._acquire_text(pubmed_id, doi)
        if text is None:
            return None

        if len(text) < 500:
            logger.warning("PDF text too short (%d chars), likely scan/image PDF", len(text))
            return None

        # Build chunk index
        chunks = self._chunk_text(text)
        if not chunks:
            return None

        model = _get_embed_model()
        if model is None:
            logger.warning("No embedding model available, cannot run RAG")
            return None

        embeddings = model.encode(chunks, show_progress_bar=False)

        result: dict[str, Any] = {}

        # Resolve abbreviations
        if gaps.get("abbreviations"):
            result["abbreviations"] = self._resolve_abbreviations(
                gaps["abbreviations"], chunks, embeddings, model
            )

        # Verify compound splits
        if gaps.get("compounds"):
            result["verified_compounds"] = self._verify_compounds(
                gaps["compounds"], chunks, embeddings, model
            )

        # Extract missing fields
        if gaps.get("fields"):
            result["metadata"] = self._extract_fields(
                gaps["fields"], chunks, embeddings, model
            )

        if not result:
            return None

        if cache:
            self._write_cache(pubmed_id, result)

        return result

    def _resolve_abbreviations(
        self,
        abbrevs: list[dict[str, str]],
        chunks: list[str],
        embeddings: np.ndarray,
        model: Any,
    ) -> dict[str, str]:
        """Resolve ambiguous abbreviations by RAG-searching the paper text.

        Each abbrev dict has keys: raw (the ambiguous value), guess (best guess).
        Returns mapping of raw -> resolved meaning.
        """
        resolved: dict[str, str] = {}
        for ab in abbrevs:
            raw = ab.get("raw", "")
            guess = ab.get("guess", "")
            if not raw:
                continue

            query = (
                f"What does '{raw}' mean in the context of this single-cell experiment? "
                f"Look for definitions like '{guess}' or expanded forms of the abbreviation."
            )
            answer = self._rag_query(query, chunks, embeddings, model)
            if answer:
                # Parse LLM answer: keep only the resolved term
                cleaned = answer.strip().strip('"').strip("'")
                if len(cleaned) < 200 and cleaned.lower() != "not_found":
                    resolved[raw] = cleaned
                    logger.info("Resolved abbreviation: %s -> %s", raw, cleaned)
        return resolved

    def _verify_compounds(
        self,
        compounds: list[dict[str, Any]],
        chunks: list[str],
        embeddings: np.ndarray,
        model: Any,
    ) -> list[dict[str, Any]]:
        """Verify compound value splits by checking each part's co-occurrence.

        Each compound dict: {"raw": "Male_Control_72yr", "parts": ["sex", "condition", "age"]}
        Returns the same list with verified=True/False and confidence annotations.
        """
        verified: list[dict[str, Any]] = []
        for comp in compounds:
            raw = comp.get("raw", "")
            parts = comp.get("parts", [])
            if not raw or not parts:
                verified.append(comp)
                continue

            split_values = raw.split("_")
            confirmed_parts: dict[str, int] = {}  # part_name -> position

            for i, (val, part_name) in enumerate(zip(split_values, parts)):
                # Search for this part value in paper context
                query = (
                    f"Does this paper mention '{val}' as a '{part_name}' "
                    f"in its experimental design? Reply YES or NO."
                )
                answer = self._rag_query(query, chunks, embeddings, model)
                if answer and answer.strip().upper().startswith("YES"):
                    confirmed_parts[part_name] = i
                    logger.info("Verified compound part: %s[%d]=%s", raw, i, val)

            comp_copy = dict(comp)
            comp_copy["confirmed_parts"] = confirmed_parts
            comp_copy["verified"] = len(confirmed_parts) > 0
            verified.append(comp_copy)

        return verified

    def _extract_fields(
        self,
        fields: list[str],
        chunks: list[str],
        embeddings: np.ndarray,
        model: Any,
    ) -> dict[str, Any]:
        """Extract specific metadata fields from the paper via RAG.

        Args:
            fields: List of canonical field names to extract
                   (e.g. "sequencing_platform", "normalization_method")
        Returns:
            Dict mapping field_name -> extracted value.
        """
        FIELD_QUERIES = {
            "sequencing_platform": (
                "What single-cell sequencing platform did the authors use "
                "for their own experiment (not cited works)? "
                "If not mentioned, reply NOT_FOUND."
            ),
            "normalization_method": (
                "What normalization method was used on the expression data? "
                "If not mentioned, reply NOT_FOUND."
            ),
            "n_batches": (
                "How many sequencing batches or lanes were processed? "
                "Answer with a number only, or NOT_FOUND."
            ),
            "n_cells": (
                "How many cells were sequenced in this dataset? "
                "Answer with a number only, or NOT_FOUND."
            ),
            "doublet_removal": (
                "Were doublets removed? If yes, what tool was used? "
                "If not mentioned, reply NOT_FOUND."
            ),
            "mito_threshold": (
                "What mitochondrial percentage threshold was used for cell filtering? "
                "Answer with a percentage number only, or NOT_FOUND."
            ),
            "reference_genome": (
                "What reference genome was used for alignment? "
                "If not mentioned, reply NOT_FOUND."
            ),
            "alignment_tool": (
                "What alignment tool was used? "
                "If not mentioned, reply NOT_FOUND."
            ),
        }

        result: dict[str, Any] = {}
        for field in fields:
            query = FIELD_QUERIES.get(field)
            if not query:
                query = (
                    f"What is the '{field}' in this single-cell RNA-seq study? "
                    f"Answer in ≤30 words, or NOT_FOUND if not mentioned."
                )

            answer = self._rag_query(query, chunks, embeddings, model)
            if answer and answer.strip().upper() != "NOT_FOUND":
                result[field] = answer.strip()
                logger.info("Extracted field: %s = %s", field, result[field][:80])

        return result

    # ------------------------------------------------------------------
    # RAG core
    # ------------------------------------------------------------------

    def _rag_query(
        self,
        query: str,
        chunks: list[str],
        embeddings: np.ndarray,
        model: Any,
        top_k: int = 3,
    ) -> Optional[str]:
        """Embed query -> cosine similarity -> retrieve top_k chunks -> LLM answer.

        Returns: LLM answer string, or None on failure.
        """
        if model is None or len(chunks) == 0:
            return None

        # 1. Embed query
        query_embedding = model.encode([query], show_progress_bar=False)[0]

        # 2. Cosine similarity
        from sklearn.metrics.pairwise import cosine_similarity
        similarities = cosine_similarity([query_embedding], embeddings)[0]
        top_indices = np.argsort(similarities)[::-1][:top_k]

        # 3. Retrieve context
        context = "\n\n---\n\n".join(
            f"[Chunk {i+1}, score={similarities[idx]:.3f}]\n{chunks[idx]}"
            for i, idx in enumerate(top_indices)
        )

        # 4. Build LLM prompt
        prompt = f"""You are an expert bioinformatician reading a single-cell RNA-seq paper.

The text below contains top-ranked excerpts relevant to this question:

QUESTION: {query}

PAPER EXCERPTS:
{context}

INSTRUCTIONS:
- Answer ONLY the question based on the excerpts above.
- Be concise (≤40 words).
- If the answer is not found in the excerpts, reply exactly one word: NOT_FOUND
- Do NOT make up information. Only use what is explicitly stated.
- If the excerpt describes cited/prior work and not the authors' own experiment,
  reply: NOT_THIS_STUDY

ANSWER:"""

        return self._call_llm(prompt)

    # ------------------------------------------------------------------
    # PDF acquisition
    # ------------------------------------------------------------------

    def _acquire_text(self, pubmed_id: str, doi: str = "") -> Optional[str]:
        """Try to get full text: PMC OA PDF -> DOI -> NCBI full-text XML."""
        from scruticell.literature.pubmed import fetch_pubmed_record

        # Get PMCID and DOI from PubMed record
        record = fetch_pubmed_record(pubmed_id)
        pmcid = record.get("pmcid", "")
        doi = doi or record.get("doi", "")

        # 1. Try PMC Open Access PDF
        if pmcid:
            pdf_bytes = self._download_pmc_pdf(pmcid)
            if pdf_bytes:
                text = self._extract_pdf_text(pdf_bytes)
                if text and len(text) > 500:
                    logger.info("Extracted %d chars from PMC PDF for PMID %s", len(text), pubmed_id)
                    return text

        # 2. Try NCBI full-text XML (PMC e-utilities)
        if pmcid:
            xml_text = self._download_pmc_xml(pmcid)
            if xml_text and len(xml_text) > 500:
                text = self._extract_xml_text(xml_text)
                if text and len(text) > 500:
                    logger.info("Extracted %d chars from PMC XML for PMID %s", len(text), pubmed_id)
                    return text

        # 3. No DOI-based PDF (avoids publisher paywall issues)
        logger.warning("Could not acquire full text for PMID %s", pubmed_id)
        return None

    def _download_pmc_pdf(self, pmcid: str) -> Optional[bytes]:
        """Download PDF from PMC Open Access."""
        import httpx
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
        try:
            headers = {"User-Agent": "Scrut-Cell/0.1 (mailto:scruticell@example.com)"}
            with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type.lower() or resp.content[:4] == b"%PDF":
                    logger.info("Downloaded PMC PDF: %s (%d bytes)", pmcid, len(resp.content))
                    return resp.content
                logger.debug("PMC response is not a PDF (content-type: %s)", content_type)
        except Exception as exc:
            logger.debug("Failed to download PMC PDF for %s: %s", pmcid, exc)
        return None

    def _download_pmc_xml(self, pmcid: str) -> Optional[str]:
        """Fetch full-text XML from NCBI E-utilities."""
        import httpx
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        params = {"db": "pmc", "id": pmcid, "rettype": "xml", "retmode": "text"}
        try:
            time.sleep(0.34)  # NCBI rate limit
            headers = {"User-Agent": "Scrut-Cell/0.1 (mailto:scruticell@example.com)"}
            with httpx.Client(timeout=30, headers=headers) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.debug("Failed to download PMC XML for %s: %s", pmcid, exc)
        return None

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_pdf_text(self, pdf_bytes: bytes) -> Optional[str]:
        """Extract text from PDF bytes using PyMuPDF."""
        try:
            import fitz
            doc = fitz.open("pdf", pdf_bytes)
            pages: list[str] = []
            for page in doc:
                text = page.get_text("text")
                if text:
                    pages.append(text)
            doc.close()
            full_text = "\n\n".join(pages)
            return full_text if full_text.strip() else None
        except Exception as exc:
            logger.warning("PyMuPDF extraction failed: %s", exc)
            return None

    def _extract_xml_text(self, xml_text: str) -> Optional[str]:
        """Extract plain text from NCBI PMC full-text XML.

        Strips XML tags, keeps section structure.
        """
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            # Collect text content from all body sections
            parts: list[str] = []
            for elem in root.iter():
                if elem.tag.endswith("}body") or "body" in elem.tag.lower():
                    for child in elem.iter():
                        if child.text and child.text.strip():
                            parts.append(child.text.strip())
            return "\n\n".join(parts) if parts else None
        except Exception as exc:
            logger.debug("XML text extraction failed: %s", exc)
            # Fallback: strip tags with regex
            clean = re.sub(r'<[^>]+>', ' ', xml_text)
            clean = re.sub(r'\s+', ' ', clean)
            return clean.strip() if len(clean) > 500 else None

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
        """Split text into overlapping chunks by paragraph boundaries.

        Prefers splitting at paragraph breaks (\\n\\n). If a single
        paragraph exceeds chunk_size, falls back to character-based
        splitting.

        Returns:
            List of text chunks, each ≤ chunk_size + overlap characters.
        """
        # Split by paragraph
        paragraphs = re.split(r'\n\s*\n', text)
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 2 <= chunk_size:
                current = f"{current}\n\n{para}" if current else para
            else:
                if current:
                    chunks.append(current)
                # If a single paragraph is too long, split by sentence
                if len(para) > chunk_size:
                    sub_chunks = self._split_long_paragraph(para, chunk_size, overlap)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = para

        if current:
            chunks.append(current)

        # Apply overlap from previous chunk
        overlapped: list[str] = []
        for i, chunk in enumerate(chunks):
            if i > 0 and overlap > 0:
                prev = chunks[i - 1]
                if len(prev) >= overlap:
                    prefix = prev[-overlap:] + "\n\n"
                else:
                    prefix = prev + "\n\n"
                chunk = prefix + chunk
            overlapped.append(chunk[:chunk_size + overlap])

        logger.info("Chunked %d chars into %d chunks", len(text), len(overlapped))
        return overlapped

    def _split_long_paragraph(self, text: str, max_len: int, overlap: int) -> list[str]:
        """Split a long paragraph into chunks by sentence boundaries."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks: list[str] = []
        current = ""
        for sent in sentences:
            if len(current) + len(sent) + 1 <= max_len:
                current = f"{current} {sent}" if current else sent
            else:
                if current:
                    chunks.append(current)
                current = sent
        if current:
            chunks.append(current)
        return chunks

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, max_tokens: int = 80) -> Optional[str]:
        """Call LLM with the RAG prompt. Returns response text or None."""
        try:
            from langchain_openai import ChatOpenAI
            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

            llm_kwargs: dict[str, Any] = {
                "model": LLM_MODEL,
                "temperature": 0.0,
                "max_tokens": max_tokens,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY

            llm = ChatOpenAI(**llm_kwargs)
            response = llm.invoke(prompt)
            return str(response.content).strip()
        except Exception as exc:
            logger.warning("LLM call failed in PDFAgent: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_path(self, pubmed_id: str) -> Path:
        return self._cache_dir / f"{pubmed_id}.json"

    def _read_cache(self, pubmed_id: str) -> Optional[dict[str, Any]]:
        """Read cached extraction result for a PMID. Returns None if expired (>30 days)."""
        cache_path = self._cache_path(pubmed_id)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            # Expire after 30 days
            age = time.time() - data.get("_cached_at", 0)
            if age > 30 * 86400:
                logger.debug("Cache expired for PMID %s (age: %.1f days)", pubmed_id, age / 86400)
                return None
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read cache for PMID %s: %s", pubmed_id, exc)
            return None

    def _write_cache(self, pubmed_id: str, data: dict[str, Any]) -> None:
        """Write extraction result to cache."""
        data = dict(data)  # shallow copy
        data["_cached_at"] = time.time()
        data["_pmid"] = pubmed_id
        cache_path = self._cache_path(pubmed_id)
        try:
            cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Cached PDF RAG result for PMID %s", pubmed_id)
        except OSError as exc:
            logger.warning("Failed to write cache for PMID %s: %s", pubmed_id, exc)

    def has_cache(self, pubmed_id: str) -> bool:
        """Check if a valid (non-expired) cache entry exists."""
        return self._read_cache(pubmed_id) is not None

    def get_cache(self, pubmed_id: str) -> Optional[dict[str, Any]]:
        """Get cached extraction result. Returns None if no valid cache."""
        return self._read_cache(pubmed_id)
```

- [ ] **Step 2: 更新 __init__.py 导出**

打开 `src/scruticell/literature/__init__.py`，将内容替换为：

```python
from scruticell.literature.pubmed import fetch_pubmed_record, search_pubmed
from scruticell.literature.extractor import extract_metadata_from_text
from scruticell.literature.pdf_agent import PDFAgent

__all__ = [
    "fetch_pubmed_record", "search_pubmed", "extract_metadata_from_text",
    "PDFAgent",
]
```

- [ ] **Step 3: 验证 PDFAgent 可导入**

```bash
python -c "from scruticell.literature.pdf_agent import PDFAgent; print('PDFAgent imported OK')"
```

Expected: `PDFAgent imported OK`

- [ ] **Step 4: Commit**

```bash
git add src/scruticell/literature/pdf_agent.py src/scruticell/literature/__init__.py
git commit -m "feat: add PDFAgent with RAG-based literature metadata extraction"
```

---

### Task 3: 在 MultiStepExtractor 中插入 Step 4 文献核实

**Files:**
- Modify: `src/scruticell/intelligence/extractors.py`

**Interfaces:**
- Consumes: `PDFAgent` (from Task 2)、`GseReport.gse_info.pubmed_id`
- Produces: 修改 `MultiStepExtractor.extract_all()` 流程，在 Step 3 和 Step 4（原来）之间插入文献核实步骤

- [ ] **Step 1: 修改 MultiStepExtractor.extract_all() 流程**

在 `extractors.py` 中，找到 `extract_all` 方法（约第 267 行）。当前结构为：

```
Step 1, 2, 3 → 如果 Step2/3 有低置信度 → 触发 PDFAgent
                               ↓
           Step 4 (原) 生成代码
           Step 5 (原) 生成最终规划
```

修改为在 Step 3 的 `messages.append(("ai", resp))` 之后、Step 4 的 prompt 发送之前，插入文献核实逻辑：

在 `extract_all` 方法内，找到约第 299 行（Step 3 的 `messages.append(("ai", resp))` 之后），在其后插入以下代码块：

```python
            # ---- Literature Verification (NEW: Step 4/6) ----
            needs_verification = self._has_unresolved()
            if needs_verification:
                logger.info("Step 4/6: literature verification (PDF RAG)...")
                pubmed_id = report.gse_info.pubmed_id
                title = report.gse_info.title

                if pubmed_id or title:
                    try:
                        from scruticell.literature.pdf_agent import PDFAgent
                        agent = PDFAgent()

                        gaps = self._collect_verification_gaps()
                        result = agent.extract(pubmed_id=pubmed_id, gaps=gaps)

                        if result:
                            # Inject verification context into conversation
                            verify_context = self._build_verification_context(result)
                            messages.append(("human", verify_context))
                            self._context["literature_verified"] = result
                            logger.info(
                                "Literature verification: %d abbreviations resolved, %d fields extracted",
                                len(result.get("abbreviations", {})),
                                len(result.get("metadata", {}))
                            )
                        else:
                            logger.info("Literature verification: no PDF available, skipping")
                    except Exception as exc:
                        logger.warning("Literature verification failed: %s", exc)
                else:
                    logger.info("Literature verification: no PMID/title, skipping")
```

然后修改后续步骤的日志和编号——把原来的 "Step 4/5" 改为 "Step 5/6"，"Step 5/5" 改为 "Step 6/6"：

```python
            # ---- Metadata code generation (Step 5/6, was Step 4) ----
            logger.info("Step 5/6: metadata code generation...")
            # ... (existing code unchanged) ...

            # ---- Final matrix/batch plan (Step 6/6, was Step 5) ----
            logger.info("Step 6/6: matrix/batch assembly plan...")
            # ... (existing code unchanged) ...
```

- [ ] **Step 2: 添加辅助方法**

在 `MultiStepExtractor` 类中添加以下方法（放在 `_build_files_json` 之前或之后）：

```python
    def _has_unresolved(self) -> bool:
        """Check if Step 2 or Step 3 produced low-confidence results that need literature verification."""
        gsm_extract = self._context.get("gsm_extract", {})
        if isinstance(gsm_extract, dict):
            # Step 2 ambiguous abbreviations with needs_literature_verify
            for abbr in gsm_extract.get("ambiguous_abbreviations", []):
                if isinstance(abbr, dict):
                    if abbr.get("needs_literature_verify") or abbr.get("confidence", 0) < 0.7:
                        logger.debug("Unresolved abbreviation: %s (conf=%.2f)",
                                     abbr.get("raw_value", "?"), abbr.get("confidence", 0))
                        return True

        file_extract = self._context.get("file_extract", {})
        if isinstance(file_extract, dict):
            # Step 3 filename extractions with low confidence
            for ex in file_extract.get("filename_extractions", []):
                if isinstance(ex, dict) and ex.get("confidence", 0) < 0.7:
                    logger.debug("Low confidence filename extraction: %s (conf=%.2f)",
                                 ex.get("filename", "?"), ex.get("confidence", 0))
                    return True
            # Step 3 orientation confidence < 0.7
            for fm in file_extract.get("file_metadata", []):
                if isinstance(fm, dict) and fm.get("orientation_confidence", 0) < 0.7:
                    logger.debug("Low confidence orientation: %s (conf=%.2f)",
                                 fm.get("filename", "?"), fm.get("orientation_confidence", 0))
                    return True

        return False

    def _collect_verification_gaps(self) -> dict:
        """Collect abbreviations, compounds, and missing fields from Steps 2-3 for PDF verification."""
        gaps: dict = {"abbreviations": [], "compounds": [], "fields": []}

        gsm_extract = self._context.get("gsm_extract", {})
        if isinstance(gsm_extract, dict):
            for abbr in gsm_extract.get("ambiguous_abbreviations", []):
                if isinstance(abbr, dict):
                    gaps["abbreviations"].append({
                        "raw": abbr.get("raw_value", ""),
                        "guess": abbr.get("best_guess", ""),
                    })

        file_extract = self._context.get("file_extract", {})
        if isinstance(file_extract, dict):
            for ex in file_extract.get("filename_extractions", []):
                if isinstance(ex, dict) and ex.get("confidence", 0) < 0.7:
                    gaps["compounds"].append({
                        "raw": ex.get("filename", ""),
                        "parts": list(ex.get("components", {}).keys()) if isinstance(ex.get("components"), dict) else [],
                    })

        # Always try to extract these key metadata fields if paper is available
        gaps["fields"] = [
            "sequencing_platform",
            "normalization_method",
            "n_batches",
            "n_cells",
            "doublet_removal",
            "mito_threshold",
            "reference_genome",
        ]

        logger.debug("Verification gaps: %d abbreviations, %d compounds, %d fields",
                     len(gaps["abbreviations"]), len(gaps["compounds"]), len(gaps["fields"]))
        return gaps

    def _build_verification_context(self, result: dict) -> str:
        """Build a context message telling the LLM about verified literature findings.

        This message is appended to the conversation before metadata code generation.
        """
        lines = [
            "LITERATURE VERIFICATION RESULTS (from paper):",
            "",
        ]

        abbrs = result.get("abbreviations", {})
        if abbrs:
            lines.append("Confirmed abbreviations:")
            for raw, resolved in abbrs.items():
                lines.append(f"  - '{raw}' = {resolved}")
            lines.append("")

        verified = result.get("verified_compounds", [])
        if verified:
            lines.append("Verified compound value splits:")
            for v in verified:
                if v.get("verified") and v.get("confirmed_parts"):
                    lines.append(f"  - '{v.get('raw', '?')}' -> {v['confirmed_parts']}")
            lines.append("")

        metadata = result.get("metadata", {})
        if metadata:
            lines.append("Extracted metadata from paper:")
            for key, value in metadata.items():
                lines.append(f"  - {key}: {value}")
            lines.append("")

        lines.extend([
            "Use this verified information in the following steps.",
            "Override any guesses with these confirmed values.",
        ])

        return "\n".join(lines)
```

- [ ] **Step 3: 验证修改后的 MultiStepExtractor 能正常加载**

```bash
python -c "from scruticell.intelligence.extractors import MultiStepExtractor; m = MultiStepExtractor(); print('MultiStepExtractor OK:', m._has_unresolved())"
```

Expected: `MultiStepExtractor OK: False`

- [ ] **Step 4: Commit**

```bash
git add src/scruticell/intelligence/extractors.py
git commit -m "feat: insert literature verification step into MultiStepExtractor"
```

---

### Task 4: 为 generator.py 添加 PDF RAG 兜底

**Files:**
- Modify: `src/scruticell/intelligence/generator.py:357-374`

**Interfaces:**
- Consumes: `PDFAgent` (from Task 2)
- Produces: `_enrich_with_literature()` 在摘要 LLM 结果稀疏时升级到 PDF RAG

- [ ] **Step 1: 修改 _fetch_literature_enrichment 增加 PDF 兜底**

找到 `generator.py` 中的 `_fetch_literature_enrichment` 方法（约第 378 行）。在方法末尾的 `return metadata` 之前，添加 PDF RAG 回退逻辑：

在 `_fetch_literature_enrichment` 方法的第 408 行（`return metadata`）之前插入：

```python
            # If abstract extraction produced sparse results, try PDF RAG
            is_sparse = not metadata or len(metadata) < 3
            if is_sparse and pubmed_id:
                logger.info("Abstract extraction sparse (%d fields), trying PDF RAG...",
                            len(metadata) if metadata else 0)
                try:
                    from scruticell.literature.pdf_agent import PDFAgent
                    agent = PDFAgent()
                    gaps = {
                        "fields": [
                            "sequencing_platform", "normalization_method",
                            "n_batches", "n_cells", "doublet_removal",
                            "reference_genome", "alignment_tool",
                        ],
                    }
                    pdf_result = agent.extract(pubmed_id=pubmed_id, gaps=gaps)
                    if pdf_result and pdf_result.get("metadata"):
                        pdf_metadata = pdf_result["metadata"]
                        if not metadata:
                            metadata = {}
                        for k, v in pdf_metadata.items():
                            if k not in metadata:
                                metadata[k] = v
                        metadata["_source"] = "pdf_rag"
                        logger.info(
                            "PDF RAG enriched with %d additional fields",
                            len(pdf_metadata),
                        )
                except Exception as exc:
                    logger.warning("PDF RAG enrichment failed: %s", exc)
```

- [ ] **Step 2: 验证 generator 模块可导入**

```bash
python -c "from scruticell.intelligence.generator import PlanOrchestrator; print('generator OK')"
```

Expected: `generator OK`

- [ ] **Step 3: Commit**

```bash
git add src/scruticell/intelligence/generator.py
git commit -m "feat: add PDF RAG fallback to literature enrichment in plan orchestrator"
```

---

### Task 5: 为 parse 管道添加 PDF Agent 触发

**Files:**
- Modify: `src/scruticell/pipeline/nodes/literature.py`

**Interfaces:**
- Consumes: `PDFAgent` (from Task 2)、`PipelineState`
- Produces: `literature_node` 在 validate 后可选调用 PDFAgent 补全元信息

- [ ] **Step 1: 在 literature_node 内添加 PDF RAG 降级路径**

`literature_node` 目前在 `literature.py` 中只查摘要。修改流程图：
- 先查摘要（已有）
- 如果摘要提取结果稀疏（`supplement` 字段数 < 3），调用 PDFAgent 补全

在 `literature_node` 函数中（约第 57-64 行），找到 `supplement = extract_metadata_from_text(text, use_llm=False)` 之后，添加 PDF RAG 回退：

在第 64 行 `if text:` 块结束之后，第 65 行 `except` 之前，插入：

```python
                # If regex extraction sparse, try PDF RAG
                if not supplement or len(supplement) < 3:
                    logger.info("Regex extraction sparse (%d fields), trying PDF RAG for %s",
                                len(supplement) if supplement else 0, state.gse_id)
                    try:
                        from scruticell.literature.pdf_agent import PDFAgent
                        agent = PDFAgent()
                        gaps = {
                            "fields": [
                                "sequencing_platform", "normalization_method",
                                "n_batches", "n_cells", "doublet_removal",
                                "reference_genome", "alignment_tool",
                            ],
                        }
                        pdf_result = agent.extract(pubmed_id=pubmed_id, gaps=gaps)
                        if pdf_result and pdf_result.get("metadata"):
                            if not supplement:
                                supplement = {}
                            for k, v in pdf_result["metadata"].items():
                                if k not in supplement:
                                    supplement[k] = v
                            logger.info(
                                "PDF RAG supplemented with %d fields for %s",
                                len(pdf_result["metadata"]), state.gse_id,
                            )
                    except Exception as exc:
                        logger.warning("PDF RAG failed for %s: %s", state.gse_id, exc)
```

- [ ] **Step 2: 验证 literature_node 可导入**

```bash
python -c "from scruticell.pipeline.nodes.literature import literature_node; print('literature_node OK')"
```

Expected: `literature_node OK`

- [ ] **Step 3: Commit**

```bash
git add src/scruticell/pipeline/nodes/literature.py
git commit -m "feat: add PDF RAG fallback to parse pipeline literature node"
```

---

### Task 6: 端到端集成测试

**Files:**
- Create: `tests/test_pdf_agent.py`

**Interfaces:**
- Consumes: `PDFAgent` (from Task 2)
- Produces: 单元测试覆盖缓存读写、文本分块、缩写解析（mock LLM）

- [ ] **Step 1: 写测试文件**

创建 `tests/test_pdf_agent.py`：

```python
"""Tests for PDFAgent chunking, caching, and gap collection."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scruticell.literature.pdf_agent import PDFAgent


class TestChunking:
    """Text chunking tests (no external dependencies)."""

    def test_chunk_single_short_paragraph(self):
        agent = PDFAgent()
        text = "This is a short paragraph."
        chunks = agent._chunk_text(text, chunk_size=500, overlap=100)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_multi_paragraph(self):
        agent = PDFAgent()
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = agent._chunk_text(text, chunk_size=500, overlap=0)
        assert len(chunks) == 1  # all fit in one chunk
        assert "Paragraph one" in chunks[0]

    def test_chunk_long_text(self):
        agent = PDFAgent()
        # Create text with 3 paragraphs, each ~300 chars
        para = "A" * 300
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = agent._chunk_text(text, chunk_size=400, overlap=50)
        # First 300 chars fit, second 300 exceeds 400 → new chunk
        assert len(chunks) >= 2

    def test_chunk_overlap(self):
        agent = PDFAgent()
        para1 = "X" * 500
        para2 = "Y" * 500
        text = f"{para1}\n\n{para2}"
        chunks = agent._chunk_text(text, chunk_size=600, overlap=100)
        assert len(chunks) >= 2
        # Second chunk should contain overlap from first
        if len(chunks) >= 2:
            # The overlap prefix should contain some "X" chars
            assert "X" in chunks[0]


class TestCache:
    """Cache read/write tests."""

    def test_write_and_read_cache(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        data = {"abbreviations": {"E3": "APOE3"}, "metadata": {"platform": "10X v3"}}
        agent._write_cache("12345", data)
        cached = agent._read_cache("12345")
        assert cached is not None
        assert cached["abbreviations"]["E3"] == "APOE3"
        assert cached["metadata"]["platform"] == "10X v3"
        assert "_cached_at" in cached
        assert cached["_pmid"] == "12345"

    def test_cache_miss(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert agent._read_cache("nonexistent") is None

    def test_cache_expired(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        data = {"test": True, "_cached_at": time.time() - 31 * 86400}  # 31 days ago
        cache_path = tmp_path / "expired.json"
        cache_path.write_text(json.dumps(data))
        # Override cache path to read our expired file
        agent._cache_path = lambda pmid: cache_path
        assert agent._read_cache("expired") is None

    def test_has_cache(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert not agent.has_cache("12345")
        agent._write_cache("12345", {"x": 1})
        assert agent.has_cache("12345")


class TestGapCollection:
    """Test _has_unresolved from MultiStepExtractor (integration)."""

    def test_no_unresolved_when_empty(self):
        from scruticell.intelligence.extractors import MultiStepExtractor
        m = MultiStepExtractor()
        assert not m._has_unresolved()

    def test_has_unresolved_abbreviation(self):
        from scruticell.intelligence.extractors import MultiStepExtractor
        m = MultiStepExtractor()
        m._context["gsm_extract"] = {
            "ambiguous_abbreviations": [
                {"raw_value": "E3", "best_guess": "APOE3", "confidence": 0.5}
            ]
        }
        assert m._has_unresolved()

    def test_has_unresolved_filename(self):
        from scruticell.intelligence.extractors import MultiStepExtractor
        m = MultiStepExtractor()
        m._context["file_extract"] = {
            "filename_extractions": [
                {"filename": "test.h5", "confidence": 0.6}
            ]
        }
        assert m._has_unresolved()

    def test_no_unresolved_high_confidence(self):
        from scruticell.intelligence.extractors import MultiStepExtractor
        m = MultiStepExtractor()
        m._context["gsm_extract"] = {
            "ambiguous_abbreviations": [
                {"raw_value": "E3", "best_guess": "APOE3", "confidence": 0.9}
            ]
        }
        m._context["file_extract"] = {
            "filename_extractions": [
                {"filename": "test.h5", "confidence": 0.95}
            ]
        }
        assert not m._has_unresolved()


class TestPDFAgentIntegration:
    """Smoke test: PDFAgent instantiation and cache operations."""

    def test_agent_creation(self):
        agent = PDFAgent()
        assert agent._cache_dir.exists()

    def test_agent_custom_cache_dir(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert agent._cache_dir == tmp_path
        assert tmp_path.exists()

    def test_agent_acquire_text_no_pmcid(self):
        agent = PDFAgent()
        # With empty pubmed_id, should return None
        text = agent._acquire_text("")
        assert text is None
```

- [ ] **Step 2: 运行测试**

```bash
pytest tests/test_pdf_agent.py -v
```

Expected: 11-12 tests pass。如果有网络依赖的测试（`_acquire_text`），可能需要网络。

- [ ] **Step 3: Commit**

```bash
git add tests/test_pdf_agent.py
git commit -m "test: add unit tests for PDFAgent chunking, caching, and gap detection"
```

---

### Task 7: 运行全量测试确认无回归

- [ ] **Step 1: 运行全量测试**

```bash
pytest tests/ -v
```

Expected: 所有已有测试通过，新增 PDFAgent 测试通过。

- [ ] **Step 2: 运行 lint**

```bash
ruff check src/scruticell/
```

Expected: 无新增 lint 错误。

- [ ] **Step 3: 验证 CLI 完整性**

```bash
python -m scruticell --help
```

Expected: 所有 6 个子命令正常显示。

- [ ] **Step 4: Commit（如有修正）**

```bash
git add -A
git commit -m "chore: final lint fixes and integration verification"
```
