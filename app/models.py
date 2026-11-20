from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["met", "partial", "not_met", "no_evidence"]
Kind = Literal["keywords", "size", "region"]
STATUS_CREDIT: dict[str, float] = {"met": 1.0, "partial": 0.5, "not_met": 0.0, "no_evidence": 0.0}


class Criterion(BaseModel):
    id: str
    label: str
    group: Literal["industry", "size", "region", "signal"]
    kind: Kind
    keywords: list[str] = Field(default_factory=list)
    min: int | None = None
    max: int | None = None
    weight: int = 1
    must_have: bool = False
    enabled: bool = True
    rationale: str = ""

    def describe(self) -> str:
        if self.kind == "size":
            return f"Company has between {self.min} and {self.max} employees."
        if self.kind == "region":
            return f"Company is based in or operates mainly in: {', '.join(self.keywords)}."
        return f"{self.label}. Indicative terms: {', '.join(self.keywords)}."


class Finding(BaseModel):
    criterion_id: str
    label: str
    status: Status
    reason: str
    quote: str | None = None
    page: str | None = None
    url: str | None = None  # public URL with text fragment (fictional .example domain)
    source_link: str | None = None  # local link that shows the cached page with the quote highlighted


class CompanyResult(BaseModel):
    id: str
    name: str
    domain: str
    website: str
    similarity: float | None
    tier: Literal["A", "B", "C"]
    score: float
    summary: str
    draft_hook: str | None
    scorer: str
    findings: list[Finding]
