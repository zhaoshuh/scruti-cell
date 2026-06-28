"""Tests for literature fetching and metadata extraction."""

import pytest

from scruticell.literature.extractor import (
    _extract_alignment,
    _extract_batch_count,
    _extract_cell_count,
    _extract_disease,
    _extract_doublet_removal,
    _extract_genome,
    _extract_mito_threshold,
    _extract_normalization,
    _extract_organism,
    _extract_platforms,
    _extract_tissue,
    extract_metadata_from_text,
)
from scruticell.literature.pubmed import _parse_pubmed_xml, _empty_record


class TestPubMedXmlParsing:
    def test_empty_record(self):
        rec = _empty_record()
        assert rec["pmid"] == ""
        assert rec["title"] == ""
        assert rec["authors"] == []

    def test_parse_minimal_xml(self):
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation Status="MEDLINE">
              <PMID Version="1">12345678</PMID>
              <Article>
                <ArticleTitle>Test single-cell RNA-seq study</ArticleTitle>
                <Abstract>
                  <AbstractText>We performed scRNA-seq on PBMCs.</AbstractText>
                </Abstract>
                <Journal>
                  <Title>Nature</Title>
                  <JournalIssue>
                    <PubDate><Year>2023</Year></PubDate>
                  </JournalIssue>
                </Journal>
                <AuthorList>
                  <Author><LastName>Smith</LastName><Initials>J</Initials></Author>
                </AuthorList>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>"""
        record = _parse_pubmed_xml(xml)
        assert record["pmid"] == "12345678"
        assert "single-cell" in record["title"]
        assert "PBMCs" in record["abstract"]
        assert record["journal"] == "Nature"
        assert record["year"] == 2023
        assert len(record["authors"]) == 1
        assert "Smith J" in record["authors"]


class TestPlatformExtraction:
    def test_10x_v3(self):
        text = "Libraries were prepared using 10X Genomics Chromium Single Cell 3' v3 chemistry."
        platforms = _extract_platforms(text)
        assert any("10X" in p for p in platforms)

    def test_smartseq2(self):
        text = "We used SMART-seq2 for full-length transcript sequencing."
        platforms = _extract_platforms(text)
        assert len(platforms) > 0

    def test_dropseq(self):
        text = "Single cells were captured using Drop-seq."
        platforms = _extract_platforms(text)
        assert "Drop_seq" in platforms

    def test_multiple_platforms(self):
        text = "We compared 10X Genomics v2 and SMART-seq2 protocols."
        platforms = _extract_platforms(text)
        assert len(platforms) >= 2

    def test_no_platform(self):
        assert _extract_platforms("No platform mentioned.") == []


class TestBatchExtraction:
    def test_three_batches(self):
        text = "Samples were processed in 3 independent batches."
        assert _extract_batch_count(text) == 3

    def test_batches_described(self):
        text = "3 batches of samples were prepared."
        assert _extract_batch_count(text) == 3

    def test_no_batch(self):
        assert _extract_batch_count("No batch information.") is None


class TestNormalizationExtraction:
    def test_raw_counts(self):
        text = "Raw UMI counts were used for downstream analysis."
        methods = _extract_normalization(text)
        assert "raw_counts" in methods or "UMI_counts" in methods

    def test_sc_transform(self):
        text = "Data were normalized using SCTransform."
        methods = _extract_normalization(text)
        assert "SCTransform" in methods

    def test_log_normalized(self):
        text = "Gene expression values were log-normalized."
        methods = _extract_normalization(text)
        assert "log_normalized" in methods

    def test_tpm(self):
        text = "Expression levels were quantified as TPM."
        methods = _extract_normalization(text)
        assert "TPM" in methods


class TestCellCountExtraction:
    def test_cells_described(self):
        assert _extract_cell_count("We profiled 15000 cells.") == 15000

    def test_with_comma(self):
        assert _extract_cell_count("A total of 12,345 cells were sequenced.") == 12345

    def test_out_of_range(self):
        assert _extract_cell_count("We found 5 cells.") is None

    def test_no_count(self):
        assert _extract_cell_count("Cells were analyzed.") is None


class TestOrganismExtraction:
    def test_human(self):
        organism = _extract_organism("Human samples were collected.")
        assert organism is not None

    def test_mouse(self):
        assert _extract_organism("Mouse brain tissue was dissected.") == "Mus musculus"

    def test_no_organism(self):
        assert _extract_organism("No species mentioned.") is None


class TestTissueExtraction:
    def test_pbmc(self):
        tissues = _extract_tissue("We isolated PBMCs from blood.")
        assert any("pbmc" in t or "blood" in t for t in tissues)

    def test_liver(self):
        tissues = _extract_tissue("Liver tissue was obtained from 5 donors.")
        assert any("liver" in t for t in tissues)

    def test_no_tissue(self):
        result = _extract_tissue("No tissue info.")
        assert len(result) <= 1


class TestAlignmentExtraction:
    def test_star(self):
        tool = _extract_alignment("Reads were aligned using STAR.")
        assert tool is not None
        assert "STAR" in tool

    def test_cell_ranger(self):
        tool = _extract_alignment("Data were processed with Cell Ranger v6.0.")
        assert tool == "Cell Ranger"

    def test_no_alignment(self):
        assert _extract_alignment("No alignment tool.") is None


class TestDoubletExtraction:
    def test_doubletfinder(self):
        result = _extract_doublet_removal(
            "Doublets were removed using DoubletFinder."
        )
        assert result is not None

    def test_scrublet(self):
        result = _extract_doublet_removal(
            "We identified doublets with Scrublet."
        )
        assert result is not None
        assert result["tool"] == "Scrublet"

    def test_no_doublet(self):
        assert _extract_doublet_removal("No doublet removal.") is None


class TestMitoExtraction:
    def test_mito_threshold(self):
        assert _extract_mito_threshold(
            "Cells with >20% mitochondrial genes were filtered."
        ) == 20

    def test_no_mito(self):
        assert _extract_mito_threshold("No mitochondrial filtering.") is None


class TestGenomeExtraction:
    def test_grch38(self):
        assert _extract_genome("Reads were aligned to GRCh38.") == "GRCh38"

    def test_mm10(self):
        assert _extract_genome("Reference genome: mm10.") == "GRCm10"

    def test_no_genome(self):
        assert _extract_genome("No genome mentioned.") is None


class TestDiseaseExtraction:
    def test_covid(self):
        diseases = _extract_disease("Patients with COVID-19 were recruited.")
        assert any("COVID" in d for d in diseases)

    def test_cancer(self):
        diseases = _extract_disease("Tumor samples from lung cancer patients.")
        assert any("cancer" in d.lower() for d in diseases) or any(
            "tumor" in d.lower() for d in diseases
        )


class TestFullExtraction:
    def test_comprehensive_text(self):
        text = """
        We performed single-cell RNA-sequencing on PBMCs from 12,500 cells
        obtained from 5 healthy human donors and 8 COVID-19 patients.
        Libraries were prepared using 10X Genomics Chromium 3' v3 chemistry.
        Samples were processed in 4 independent batches.
        Raw UMI counts were used for analysis.
        Doublets were removed using Scrublet.
        Cells with >15% mitochondrial gene content were excluded.
        Reads were aligned to the GRCh38 reference genome using Cell Ranger v6.1.
        """
        result = extract_metadata_from_text(text)

        platforms = result.get("sequencing_platform", [])
        assert any("10X" in p for p in platforms)
        assert result.get("n_batches_described") == 4
        assert "UMI_counts" in result.get("normalization_method", [])
        assert result.get("n_cells_described") == 12500
        assert result.get("doublet_removal") is True
        assert result.get("mito_threshold") == 15
        assert result.get("alignment_tool") is not None
        assert result.get("reference_genome") == "GRCh38"

    def test_empty_text(self):
        result = extract_metadata_from_text("")
        assert result == {}
