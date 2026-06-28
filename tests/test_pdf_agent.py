"""Tests for PDFAgent chunking, caching, and gap detection."""
import json
import time
from pathlib import Path

import pytest

from scruticell.literature.pdf_agent import PDFAgent


class TestChunking:
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
        assert len(chunks) == 1

    def test_chunk_long_text(self):
        agent = PDFAgent()
        para = "A" * 300
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = agent._chunk_text(text, chunk_size=400, overlap=50)
        assert len(chunks) >= 2

    def test_chunk_overlap(self):
        agent = PDFAgent()
        para1 = "X" * 500
        para2 = "Y" * 500
        text = f"{para1}\n\n{para2}"
        chunks = agent._chunk_text(text, chunk_size=600, overlap=100)
        assert len(chunks) >= 2


class TestCache:
    def test_write_and_read_cache(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        data = {"abbreviations": {"E3": "APOE3"}, "metadata": {"platform": "10X v3"}}
        agent._write_cache("12345", data)
        cached = agent._read_cache("12345")
        assert cached is not None
        assert cached["abbreviations"]["E3"] == "APOE3"

    def test_cache_miss(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert agent._read_cache("nonexistent") is None

    def test_cache_expired(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        cache_path = tmp_path / "expired.json"
        data = {"test": True, "_cached_at": time.time() - 31 * 86400}
        cache_path.write_text(json.dumps(data))
        agent._cache_path = lambda pmid: cache_path
        assert agent._read_cache("expired") is None

    def test_has_cache(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert not agent.has_cache("12345")
        agent._write_cache("12345", {"x": 1})
        assert agent.has_cache("12345")


class TestGapCollection:
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
    def test_agent_creation(self):
        agent = PDFAgent()
        assert agent._cache_dir.exists()

    def test_agent_custom_cache_dir(self, tmp_path):
        agent = PDFAgent(cache_dir=tmp_path)
        assert agent._cache_dir == tmp_path

    def test_agent_acquire_text_no_pmcid(self):
        agent = PDFAgent()
        text = agent._acquire_text("")
        assert text is None
