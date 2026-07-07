"""PubMed API fetching via Biopython Entrez and httpx fallback.

Fetches article metadata (title, abstract, authors, journal, methods)
from PubMed given a PMID or search query.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from scruticell.config import BROWSER_HEADERS

logger = logging.getLogger(__name__)

PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

BIOPYTHON_AVAILABLE = False
try:
    from Bio import Entrez
    BIOPYTHON_AVAILABLE = True
except ImportError:
    pass

HTTPX_AVAILABLE = False
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    logger.warning("httpx not installed; PubMed HTTP fetching unavailable")


def _get_ncbi_key() -> str:
    """Lazy-load NCBI API key from config to avoid circular import."""
    from scruticell.config import NCBI_API_KEY
    return NCBI_API_KEY


def fetch_pubmed_record(
    pubmed_id: str,
    email: str = "scruticell@example.com",
) -> dict[str, Any]:
    """Fetch a PubMed record by PMID."""
    xml_text = _fetch_pubmed_xml(pubmed_id, email)
    if xml_text:
        return _parse_pubmed_xml(xml_text)
    return _empty_record()


def _fetch_pubmed_xml(pubmed_id: str, email: str = "") -> Optional[str]:
    api_key = _get_ncbi_key()
    if BIOPYTHON_AVAILABLE:
        try:
            Entrez.email = email or "scruticell@example.com"
            Entrez.api_key = api_key
            handle = Entrez.efetch(db="pubmed", id=pubmed_id, rettype="xml", retmode="text")
            data = handle.read()
            handle.close()
            return data
        except Exception as exc:
            logger.warning("Biopython Entrez fetch failed: %s", exc)

    if HTTPX_AVAILABLE:
        try:
            params = {"db": "pubmed", "id": pubmed_id, "rettype": "xml", "retmode": "text",
                      "api_key": api_key}
            time.sleep(0.34)
            import httpx
            with httpx.Client(timeout=30, headers=BROWSER_HEADERS) as client:
                resp = client.get(PUBMED_EFETCH_URL, params=params)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.warning("httpx PubMed fetch failed: %s", exc)

    return None


def _parse_pubmed_xml(xml_text: str) -> dict[str, Any]:
    record: dict[str, Any] = _empty_record()

    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception:
        return record

    article = root.find(".//PubmedArticle")
    if article is None:
        return record

    medline = article.find(".//MedlineCitation")
    if medline is None:
        return record

    pmid_el = medline.find(".//PMID")
    if pmid_el is not None and pmid_el.text:
        record["pmid"] = pmid_el.text

    art = medline.find(".//Article")
    if art is not None:
        title_el = art.find(".//ArticleTitle")
        if title_el is not None and title_el.text:
            record["title"] = title_el.text

        abstract_parts = art.findall(".//AbstractText")
        if abstract_parts:
            record["abstract"] = "\n".join((p.text or "") for p in abstract_parts)

        journal_el = art.find(".//Journal")
        if journal_el is not None:
            jtitle = journal_el.find(".//Title")
            if jtitle is not None and jtitle.text:
                record["journal"] = jtitle.text
            year_el = journal_el.find(".//PubDate/Year")
            if year_el is not None and year_el.text:
                record["year"] = int(year_el.text)

        authors: list[str] = []
        for author in art.findall(".//Author"):
            last = author.findtext("LastName")
            init = author.findtext("Initials")
            if last:
                authors.append(f"{last} {init}" if init else last)
        record["authors"] = authors

    eid = article.find(".//ELocationID[@EIdType='doi']")
    if eid is not None and eid.text:
        record["doi"] = eid.text

    pmc = article.find(".//OtherID[@Source='PMC']")
    if pmc is not None and pmc.text:
        record["pmcid"] = pmc.text

    mesh_terms: list[str] = []
    for mesh in medline.findall(".//MeshHeading/DescriptorName"):
        if mesh.text:
            mesh_terms.append(mesh.text)
    record["mesh_terms"] = mesh_terms

    pub_types: list[str] = []
    for pt in medline.findall(".//PublicationType"):
        if pt.text:
            pub_types.append(pt.text)
    record["publication_types"] = pub_types

    record["raw_xml"] = xml_text
    return record


def _empty_record() -> dict[str, Any]:
    return {
        "pmid": "", "title": "", "abstract": "", "authors": [],
        "journal": "", "year": 0, "doi": "", "pmcid": "",
        "mesh_terms": [], "publication_types": [], "raw_xml": "",
    }


def search_pubmed(
    query: str,
    email: str = "scruticell@example.com",
    retmax: int = 5,
) -> list[str]:
    """Search PubMed and return a list of PMIDs."""
    api_key = _get_ncbi_key()
    if BIOPYTHON_AVAILABLE:
        try:
            Entrez.email = email
            Entrez.api_key = api_key
            handle = Entrez.esearch(db="pubmed", term=query, retmax=retmax)
            result = Entrez.read(handle)
            handle.close()
            return list(result.get("IdList", []))
        except Exception as exc:
            logger.warning("Biopython search failed: %s", exc)

    if HTTPX_AVAILABLE:
        try:
            params = {"db": "pubmed", "term": query, "retmax": str(retmax), "retmode": "json",
                      "api_key": api_key}
            time.sleep(0.34)
            import httpx
            with httpx.Client(timeout=30, headers=BROWSER_HEADERS) as client:
                resp = client.get(PUBMED_ESEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                return list(data.get("esearchresult", {}).get("idlist", []))
        except Exception as exc:
            logger.warning("httpx PubMed search failed: %s", exc)

    return []
