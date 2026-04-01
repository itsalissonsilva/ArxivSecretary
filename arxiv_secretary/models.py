from __future__ import annotations

from dataclasses import dataclass, field


WATCH_TYPES = ("author", "institution", "topic")


@dataclass(slots=True)
class WatchItem:
    id: int | None
    kind: str
    label: str
    query: str
    notes: str = ""


@dataclass(slots=True)
class Paper:
    entry_id: str
    title: str
    summary: str
    published: str
    updated: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    pdf_url: str
    abstract_url: str
    comment: str = ""
    matched_watch_labels: set[str] = field(default_factory=set)

    @property
    def short_authors(self) -> str:
        if not self.authors:
            return "Unknown"
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{', '.join(self.authors[:3])}, +{len(self.authors) - 3} more"
