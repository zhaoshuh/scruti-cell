"""PDF Agent: download paper PDF, chunk, embed locally, RAG-query LLM.

Flow:
1. Acquire PDF (PMC OA -> NCBI full-text XML fallback)
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
from typing import Any

import numpy as np

from scruticell.utils.tokens import invoke_and_track

logger = logging.getLogger(__name__)

_CACHE_ROOT = Path("G:/scruticell/cache/pdf")

_EMBED_MODEL = None
_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"


def _get_embed_model() -> Any:
    """Lazy-load the sentence-transformers model (shared across all PDFAgent instances)."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", _EMBED_MODEL_NAME)
            _EMBED_MODEL = SentenceTransformer(_EMBED_MODEL_NAME)
            logger.info("Embedding model loaded (%d dimensions)",
                        _EMBED_MODEL.get_sentence_embedding_dimension())
        except Exception as exc:
            logger.warning("Failed to load embedding model: %s. PDF RAG unavailable.", exc)
            return None
    return _EMBED_MODEL


class PDFAgent:
    """Download, chunk, embed, and RAG-query scientific PDFs for metadata extraction."""

    def __init__(self, cache_dir: Path | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_ROOT
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def extract(
        self,
        pubmed_id: str,
        doi: str = "",
        gaps: dict[str, Any] | None = None,
        cache: bool = True,
    ) -> dict[str, Any] | None:
        """Full pipeline: acquire PDF -> per-item ORC verification -> verified_items."""
        gaps = gaps or {}

        if cache:
            cached = self._read_cache(pubmed_id)
            if cached is not None:
                logger.info("PDF RAG cache hit for PMID %s", pubmed_id)
                return cached

        text = self._acquire_text(pubmed_id, doi)
        if text is None:
            return None

        if len(text) < 500:
            logger.warning("PDF text too short (%d chars)", len(text))
            return None

        chunks = self._chunk_text(text)
        if not chunks:
            return None

        model = _get_embed_model()
        if model is None:
            logger.warning("No embedding model available")
            return None

        embeddings = model.encode(chunks, show_progress_bar=False)

        # Per-item ORC verification with GSM/file context
        items = gaps.get("items", [])
        verified_items: list[dict[str, Any]] = []

        if items:
            verified_items = self._verify_items(items, chunks, embeddings, model, pubmed_id)
        else:
            # Fallback to old-style gaps format
            result: dict[str, Any] = {}
            if gaps.get("abbreviations"):
                result["abbreviations"] = self._resolve_abbreviations(
                    gaps["abbreviations"], chunks, embeddings, model,
                )
            if gaps.get("compounds"):
                result["verified_compounds"] = self._verify_compounds(
                    gaps["compounds"], chunks, embeddings, model,
                )
            if gaps.get("fields"):
                result["metadata"] = self._extract_fields(
                    gaps["fields"], chunks, embeddings, model,
                )
            if result:
                if cache:
                    self._write_cache(pubmed_id, result)
                return result
            return None

        result = {"verified_items": verified_items}

        if cache:
            self._write_cache(pubmed_id, result)

        return result

    def _verify_items(
        self, items: list[dict[str, Any]],
        chunks: list[str], embeddings: np.ndarray, model: Any,
        pubmed_id: str,
    ) -> list[dict[str, Any]]:
        """Per-item ORC verification: each uncertain item gets a targeted RAG query."""
        verified: list[dict[str, Any]] = []

        for item in items:
            raw = item.get("raw_value", "")
            if not raw:
                continue

            guess = item.get("best_guess", "")
            gsm_id = item.get("gsm_id", "")
            source_step = item.get("source_step", "")
            source_field = item.get("source_field", "")
            filename = item.get("filename", "")
            context = item.get("context", "")

            # Build context-rich query
            query_parts = [f"What does '{raw}' mean in the context of this scRNA-seq experiment?"]
            if gsm_id:
                query_parts.append(f"This value was found in GSM sample '{gsm_id}'.")
            if source_field:
                query_parts.append(f"It appears in the field '{source_field}'.")
            if filename:
                query_parts.append(f"The source file is '{filename}'.")
            if guess:
                query_parts.append(f"Best guess: '{guess}'.")

            query = " ".join(query_parts)
            query += " Reply with the resolved meaning ONLY (concise, <100 chars). If not found in the paper, reply: UNCERTAIN."

            answer = self._rag_query(query, chunks, embeddings, model)
            resolved = ""
            confidence = 0.0

            if answer:
                answer_upper = answer.strip().upper()
                if answer_upper != "NOT_FOUND" and answer_upper != "UNCERTAIN":
                    resolved = answer.strip().strip('"').strip("'")
                    if len(resolved) < 200:
                        confidence = 0.95
                        logger.info(
                            "ORC verified: '%s' -> '%s' [GSM=%s, src=%s]",
                            raw, resolved, gsm_id, source_step,
                        )
                    else:
                        resolved = ""
                        confidence = 0.0

            verified.append({
                "raw_value": raw,
                "resolved": resolved or guess,
                "confidence": confidence if resolved else item.get("confidence", 0) * 0.5,
                "gsm_id": gsm_id,
                "source_field": source_field,
                "source_step": source_step,
                "filename": filename,
                "verified_by": f"literature_pmid_{pubmed_id}",
                "context": context,
            })

        return verified

    def _resolve_abbreviations(
        self, abbrevs: list[dict[str, str]],
        chunks: list[str], embeddings: np.ndarray, model: Any,
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for ab in abbrevs:
            raw = ab.get("raw", "")
            if not raw:
                continue
            guess = ab.get("guess", "")
            query = (
                f"What does '{raw}' mean in this single-cell experiment? "
                f"Look for definitions like '{guess}' or expanded forms."
            )
            answer = self._rag_query(query, chunks, embeddings, model)
            if answer:
                cleaned = answer.strip().strip('"').strip("'")
                if len(cleaned) < 200 and cleaned.lower() != "not_found":
                    resolved[raw] = cleaned
                    logger.info("Resolved abbreviation: %s -> %s", raw, cleaned)
        return resolved

    def _verify_compounds(
        self, compounds: list[dict[str, Any]],
        chunks: list[str], embeddings: np.ndarray, model: Any,
    ) -> list[dict[str, Any]]:
        verified: list[dict[str, Any]] = []
        for comp in compounds:
            raw = comp.get("raw", "")
            parts = comp.get("parts", [])
            if not raw or not parts:
                verified.append(comp)
                continue

            split_values = raw.split("_")
            confirmed_parts: dict[str, int] = {}

            for i, (val, part_name) in enumerate(zip(split_values, parts)):
                query = (
                    f"Does this paper mention '{val}' as a '{part_name}' "
                    f"in its experimental design? Reply YES or NO."
                )
                answer = self._rag_query(query, chunks, embeddings, model)
                if answer and answer.strip().upper().startswith("YES"):
                    confirmed_parts[part_name] = i

            comp_copy = dict(comp)
            comp_copy["confirmed_parts"] = confirmed_parts
            comp_copy["verified"] = len(confirmed_parts) > 0
            verified.append(comp_copy)
        return verified

    def _extract_fields(
        self, fields: list[str],
        chunks: list[str], embeddings: np.ndarray, model: Any,
    ) -> dict[str, Any]:
        field_queries = {
            "sequencing_platform": (
                "What single-cell sequencing platform did the authors use? "
                "Reply concisely or NOT_FOUND."
            ),
            "normalization_method": (
                "What normalization method was used on the expression data? "
                "Reply concisely or NOT_FOUND."
            ),
            "n_batches": (
                "How many sequencing batches or lanes were processed? "
                "Answer with a number or NOT_FOUND."
            ),
            "n_cells": (
                "How many cells were sequenced? Number or NOT_FOUND."
            ),
            "doublet_removal": (
                "Were doublets removed? What tool? Reply concisely or NOT_FOUND."
            ),
            "mito_threshold": (
                "What mitochondrial percentage threshold was used for filtering? "
                "Number or NOT_FOUND."
            ),
            "reference_genome": (
                "What reference genome was used for alignment? Reply or NOT_FOUND."
            ),
            "alignment_tool": (
                "What alignment tool was used? Reply or NOT_FOUND."
            ),
        }

        result: dict[str, Any] = {}
        for field in fields:
            query = field_queries.get(field)
            if not query:
                query = f"What is the '{field}'? Reply concisely or NOT_FOUND."
            answer = self._rag_query(query, chunks, embeddings, model)
            if answer and answer.strip().upper() != "NOT_FOUND":
                result[field] = answer.strip()
        return result

    def _rag_query(
        self, query: str, chunks: list[str],
        embeddings: np.ndarray, model: Any, top_k: int = 3,
    ) -> str | None:
        if model is None or len(chunks) == 0:
            return None

        query_embedding = model.encode([query], show_progress_bar=False)[0]
        from sklearn.metrics.pairwise import cosine_similarity
        similarities = cosine_similarity([query_embedding], embeddings)[0]
        top_indices = np.argsort(similarities)[::-1][:top_k]

        context = "\n\n---\n\n".join(
            f"[Chunk {i+1}, score={similarities[idx]:.3f}]\n{chunks[idx]}"
            for i, idx in enumerate(top_indices)
        )

        prompt = f"""You are an expert bioinformatician reading a scRNA-seq paper.

QUESTION: {query}

PAPER EXCERPTS:
{context}

INSTRUCTIONS:
- Answer ONLY based on the excerpts above.
- Be concise (<40 words).
- If not found in excerpts, reply: NOT_FOUND
- Do not invent information.

ANSWER:"""

        return self._call_llm(prompt)

    def _acquire_text(self, pubmed_id: str, doi: str = "") -> str | None:
        from scruticell.literature.pubmed import fetch_pubmed_record
        record = fetch_pubmed_record(pubmed_id)
        pmcid = record.get("pmcid", "")
        doi = doi or record.get("doi", "")

        if pmcid:
            pdf_bytes = self._download_pmc_pdf(pmcid)
            if pdf_bytes:
                text = self._extract_pdf_text(pdf_bytes)
                if text and len(text) > 500:
                    logger.info("Extracted %d chars from PMC PDF", len(text))
                    return text

        if pmcid:
            xml_text = self._download_pmc_xml(pmcid)
            if xml_text and len(xml_text) > 500:
                text = self._extract_xml_text(xml_text)
                if text and len(text) > 500:
                    logger.info("Extracted %d chars from PMC XML", len(text))
                    return text

        logger.warning("Could not acquire full text for PMID %s", pubmed_id)
        return None

    def _download_pmc_pdf(self, pmcid: str) -> bytes | None:
        import httpx
        from scruticell.config import BROWSER_HEADERS, NCBI_API_KEY
        url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
        try:
            params = {"api_key": NCBI_API_KEY} if NCBI_API_KEY else {}
            with httpx.Client(
                timeout=30, follow_redirects=True, headers=BROWSER_HEADERS,
            ) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "pdf" in content_type.lower() or resp.content[:4] == b"%PDF":
                    return resp.content
        except Exception as exc:
            logger.debug("PMC PDF download failed: %s", exc)
        return None

    def _download_pmc_xml(self, pmcid: str) -> str | None:
        import httpx
        from scruticell.config import BROWSER_HEADERS, NCBI_API_KEY
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        params = {"db": "pmc", "id": pmcid, "rettype": "xml", "retmode": "text",
                  "api_key": NCBI_API_KEY}
        try:
            time.sleep(0.34)
            with httpx.Client(timeout=30, headers=BROWSER_HEADERS) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.debug("PMC XML download failed: %s", exc)
        return None

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str | None:
        try:
            import fitz
            doc = fitz.open("pdf", pdf_bytes)
            pages: list[str] = []
            for page in doc:
                text = page.get_text("text")
                if text:
                    pages.append(text)
            doc.close()
            return "\n\n".join(pages)
        except Exception as exc:
            logger.warning("PyMuPDF extraction failed: %s", exc)
            return None

    def _extract_xml_text(self, xml_text: str) -> str | None:
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            parts: list[str] = []
            for elem in root.iter():
                if "body" in elem.tag.lower() or elem.tag.endswith("}body"):
                    for child in elem.iter():
                        if child.text and child.text.strip():
                            parts.append(child.text.strip())
            return "\n\n".join(parts) if parts else None
        except Exception:
            clean = re.sub(r'<[^>]+>', ' ', xml_text)
            clean = re.sub(r'\s+', ' ', clean)
            return clean.strip() if len(clean) > 500 else None

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
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
                if len(para) > chunk_size:
                    chunks.extend(self._split_long_paragraph(para, chunk_size, overlap))
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)

        overlapped: list[str] = []
        for i, chunk in enumerate(chunks):
            if i > 0 and overlap > 0:
                prev = chunks[i - 1]
                prefix = (prev[-overlap:] + "\n\n") if len(prev) >= overlap else (prev + "\n\n")
                chunk = prefix + chunk
            overlapped.append(chunk[:chunk_size + overlap])
        return overlapped

    def _split_long_paragraph(self, text: str, max_len: int, _overlap: int) -> list[str]:
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

    def _call_llm(self, prompt: str, max_tokens: int = 80) -> str | None:
        try:
            from langchain_openai import ChatOpenAI

            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
            llm_kwargs: dict[str, Any] = {
                "model": LLM_MODEL, "temperature": 0.0, "max_tokens": max_tokens,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY
            llm = ChatOpenAI(**llm_kwargs)
            response = invoke_and_track(llm, prompt, "literature_rag")
            return str(response.content).strip()
        except Exception as exc:
            logger.warning("LLM call failed in PDFAgent: %s", exc)
            return None

    def _cache_path(self, pubmed_id: str) -> Path:
        return self._cache_dir / f"{pubmed_id}.json"

    def _read_cache(self, pubmed_id: str) -> dict[str, Any] | None:
        cache_path = self._cache_path(pubmed_id)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            age = time.time() - data.get("_cached_at", 0)
            if age > 30 * 86400:
                return None
            return data
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, pubmed_id: str, data: dict[str, Any]) -> None:
        data = dict(data)
        data["_cached_at"] = time.time()
        data["_pmid"] = pubmed_id
        cache_path = self._cache_path(pubmed_id)
        try:
            cache_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
            )
        except OSError:
            pass

    def has_cache(self, pubmed_id: str) -> bool:
        return self._read_cache(pubmed_id) is not None

    def get_cache(self, pubmed_id: str) -> dict[str, Any] | None:
        return self._read_cache(pubmed_id)
