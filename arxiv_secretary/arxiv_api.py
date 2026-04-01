from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .models import Paper, WatchItem


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "arxiv-secretary/0.1 (desktop-app)"


def _escape_query_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').strip()


def build_search_query(item: WatchItem) -> str:
    value = _escape_query_text(item.query)
    if item.kind == "author":
        return f'au:"{value}"'
    return f'all:"{value}"'


def fetch_matches(
    item: WatchItem,
    *,
    max_results: int = 25,
    only_last_days: int | None = None,
    timeout: int = 20,
) -> list[Paper]:
    if item.kind == "author":
        papers = _fetch_author_matches(item.query, max_results=max_results, timeout=timeout)
    else:
        search_query = build_search_query(item)
        papers = _fetch_query(search_query, max_results=max_results, timeout=timeout)
    if only_last_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=only_last_days)
        papers = [paper for paper in papers if parse_arxiv_datetime(paper.published) >= cutoff]
    return [replace(paper, matched_watch_labels={item.label}) for paper in papers]


def parse_feed(payload: bytes) -> list[Paper]:
    root = ET.fromstring(payload)
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = _text(entry.find("atom:id", ATOM_NS))
        title = _clean_text(_text(entry.find("atom:title", ATOM_NS)))
        summary = _clean_text(_text(entry.find("atom:summary", ATOM_NS)))
        published = _text(entry.find("atom:published", ATOM_NS))
        updated = _text(entry.find("atom:updated", ATOM_NS))
        authors = [_text(author.find("atom:name", ATOM_NS)) for author in entry.findall("atom:author", ATOM_NS)]
        categories = [category.attrib.get("term", "") for category in entry.findall("atom:category", ATOM_NS)]
        primary = entry.find("arxiv:primary_category", ATOM_NS)
        primary_category = primary.attrib.get("term", "") if primary is not None else ""
        comment = _clean_text(_text(entry.find("arxiv:comment", ATOM_NS)))
        pdf_url = ""
        abstract_url = entry_id
        for link in entry.findall("atom:link", ATOM_NS):
            href = link.attrib.get("href", "")
            title_attr = link.attrib.get("title", "")
            rel = link.attrib.get("rel", "")
            if title_attr == "pdf":
                pdf_url = href
            elif rel == "alternate" and href:
                abstract_url = href
        papers.append(
            Paper(
                entry_id=entry_id,
                title=title,
                summary=summary,
                published=published,
                updated=updated,
                authors=authors,
                categories=[value for value in categories if value],
                primary_category=primary_category,
                pdf_url=pdf_url,
                abstract_url=abstract_url,
                comment=comment,
            )
        )
    return papers


def parse_arxiv_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _fetch_author_matches(query: str, *, max_results: int, timeout: int) -> list[Paper]:
    exact_query = f'au:"{_escape_query_text(query)}"'
    exact_results = _fetch_query(exact_query, max_results=max_results, timeout=timeout)
    if exact_results:
        return exact_results

    surname = _extract_surname(query)
    if not surname:
        return []

    fallback_limit = max(100, max_results * 12)
    broad_results = _fetch_query(f'au:{surname}', max_results=fallback_limit, timeout=timeout)
    filtered = [paper for paper in broad_results if _paper_matches_author_query(paper, query)]
    return filtered[:max_results]


def _fetch_query(search_query: str, *, max_results: int, timeout: int) -> list[Paper]:
    url = (
        f"{API_URL}?search_query={quote(search_query)}"
        f"&start=0&max_results={max_results}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        content = response.read()
    return parse_feed(content)


def _paper_matches_author_query(paper: Paper, query: str) -> bool:
    return any(_author_name_matches(author_name, query) for author_name in paper.authors)


def _author_name_matches(author_name: str, query: str) -> bool:
    normalized_author = _normalize_person_name(author_name)
    normalized_query = _normalize_person_name(query)
    if not normalized_author or not normalized_query:
        return False

    author_tokens = normalized_author.split()
    query_tokens = normalized_query.split()
    if not author_tokens or not query_tokens:
        return False
    if author_tokens[-1] != query_tokens[-1]:
        return False
    if normalized_query in normalized_author or normalized_author in normalized_query:
        return True
    if SequenceMatcher(None, normalized_query, normalized_author).ratio() >= 0.78:
        return True

    query_first = query_tokens[0]
    author_first = author_tokens[0]
    if query_first[:1] == author_first[:1] and SequenceMatcher(None, query_first, author_first).ratio() >= 0.55:
        return True
    return False


def _extract_surname(query: str) -> str:
    tokens = _normalize_person_name(query).split()
    return tokens[-1] if tokens else ""


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_person_name(value: str) -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())
