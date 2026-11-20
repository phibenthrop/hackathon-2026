"""Scorers: judge each candidate against each ICP criterion, with evidence.

* ``RuleScorer``  deterministic keyword/regex scorer. Used when no LLM key is set.
* ``LLMScorer``   asks an OpenAI-compatible model; every quote it returns is
                  checked verbatim against the website text and discarded if absent.

Both produce ``Finding`` objects; the tier is computed from the findings by the
same deterministic function, so the model never sets the tier directly.
No verdict without evidence: a finding without a verified quote is "no_evidence".
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from .evidence import find_sizes, find_terms, fragment_url, local_source_link, verify_quote
from .icp import GAZETTEER
from .llm import LLMClient, LLMConfig
from .models import STATUS_CREDIT, CompanyResult, Criterion, Finding
from .sources import Website

log = logging.getLogger("icp.scoring")

TIER_A = 0.75
TIER_B = 0.45

HOOKS = {
    "sig_manual": "your team still records temperatures by hand. ColdTrace sensors write those records automatically.",
    "sig_compliance": "audits are part of your routine. ColdTrace turns sensor data into audit-ready temperature reports.",
    "sig_expansion": "you are adding capacity. Every new cold room could be monitored from day one.",
    "sig_hiring": "you are growing the team. Automatic temperature records mean fewer manual checks per shift.",
}


class Scorer(Protocol):
    name: str

    def findings(self, site: Website, criteria: list[Criterion]) -> list[Finding]: ...


# ---------------------------------------------------------------- rule-based

class RuleScorer:
    name = "rules"

    def findings(self, site: Website, criteria: list[Criterion]) -> list[Finding]:
        return [self._one(site, c) for c in criteria]

    def _one(self, site: Website, c: Criterion) -> Finding:
        base = {"criterion_id": c.id, "label": c.label}
        if c.kind == "size":
            sizes = find_sizes(site)
            if not sizes:
                return Finding(**base, status="no_evidence", reason="No headcount stated on the website.")
            page, sent, n = sizes[0]
            lo, hi = c.min or 0, c.max or 10**9
            if lo <= n <= hi:
                st, why = "met", f"States {n:,} employees; target {lo}-{hi}."
            elif lo / 2 <= n <= hi * 2:
                st, why = "partial", f"States {n:,} employees; close to target {lo}-{hi}."
            else:
                st, why = "not_met", f"States {n:,} employees; outside target {lo}-{hi}."
            return Finding(**base, status=st, reason=why, quote=sent, page=page)

        if c.kind == "region":
            hits = find_terms(site, c.keywords)
            if hits:
                h = hits[0]
                return Finding(**base, status="met", reason=f"Located in target region ({h.term}).",
                               quote=h.sentence, page=h.page)
            targets = {k.lower() for k in c.keywords}
            foreign = [p for places in GAZETTEER.values() for p in places if p.lower() not in targets]
            fh = find_terms(site, foreign)
            if fh:
                h = fh[0]
                return Finding(**base, status="not_met", reason=f"Points to {h.term}, outside the target region.",
                               quote=h.sentence, page=h.page)
            return Finding(**base, status="no_evidence", reason="No location found on the website.")

        hits = find_terms(site, c.keywords)
        positive = [h for h in hits if not h.negated]
        if positive:
            h = positive[0]
            distinct = list(dict.fromkeys(x.term.lower().replace("-", " ") for x in positive))
            if c.min and len(distinct) < c.min:  # breadth required: e.g. at least 2 different niche terms
                return Finding(**base, status="partial",
                               reason=f'Website mentions only "{h.term}" ({len(distinct)} of {c.min} terms needed).',
                               quote=h.sentence, page=h.page)
            return Finding(**base, status="met", reason=f'Website mentions "{h.term}".',
                           quote=h.sentence, page=h.page)
        if hits:
            h = hits[0]
            return Finding(**base, status="not_met", reason=f'Website rules it out ("{h.term}" in a negated sentence).',
                           quote=h.sentence, page=h.page)
        return Finding(**base, status="no_evidence", reason="No evidence found on the website.")


# ---------------------------------------------------------------- LLM-based

SYSTEM_PROMPT = """You qualify B2B leads against an ideal customer profile (ICP).
Use ONLY the website text you are given. For every criterion return:
- status: one of "met", "partial", "not_met", "no_evidence"
- reason: one short sentence
- quote: a passage copied CHARACTER FOR CHARACTER from the website text that proves the status (max 40 words), or null
- page: the page path the quote comes from, or null
If the text does not support a judgement, use "no_evidence" with quote null. Never infer facts that are not written.
Reply with JSON only: {"findings": [{"criterion_id": "...", "status": "...", "reason": "...", "quote": "...", "page": "..."}]}"""


class LLMScorer:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = LLMClient(config)
        self.name = f"llm:{config.model}"
        self._fallback = RuleScorer()

    def _prompt(self, site: Website, criteria: list[Criterion]) -> list[dict]:
        crit = "\n".join(f"- {c.id}: {c.describe()}" for c in criteria)
        pages = "\n\n".join(f"=== PAGE {p} ===\n{t}" for p, t in site.pages.items())
        user = f"Company: {site.company.name} ({site.company.domain})\n\nCRITERIA:\n{crit}\n\nWEBSITE TEXT:\n{pages}"
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    def findings(self, site: Website, criteria: list[Criterion]) -> list[Finding]:
        raw = None
        for _attempt in range(2):
            try:
                raw = json.loads(self.client.chat(self._prompt(site, criteria)))
                break
            except json.JSONDecodeError:
                continue
            except Exception as exc:  # network, auth, timeout: fall back, never block the demo
                log.warning("LLM call failed for %s: %s", site.company.domain, exc)
                break
        if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
            out = self._fallback.findings(site, criteria)
            for f in out:
                f.reason += " (rule fallback: LLM unavailable)"
            return out

        by_id = {str(f.get("criterion_id")): f for f in raw["findings"] if isinstance(f, dict)}
        out = []
        for c in criteria:
            f = by_id.get(c.id, {})
            status = f.get("status")
            if status not in STATUS_CREDIT:
                status = "no_evidence"
            quote = f.get("quote") if isinstance(f.get("quote"), str) else None
            out.append(Finding(criterion_id=c.id, label=c.label, status=status,
                               reason=str(f.get("reason") or "No verdict returned by the model."),
                               quote=quote, page=f.get("page") if isinstance(f.get("page"), str) else None))
        return out


# ---------------------------------------------------------------- shared

def enforce_evidence(site: Website, findings: list[Finding]) -> list[Finding]:
    """Gate: keep a verdict only if its quote occurs verbatim on the site."""
    for f in findings:
        if f.status == "no_evidence":
            f.quote = f.page = None
            continue
        page = verify_quote(site, f.quote, f.page)
        if page is None:
            f.status = "no_evidence"
            f.reason = "Quote not found verbatim in the website text; verdict discarded."
            f.quote = f.page = None
            continue
        f.page = page
        f.url = fragment_url(site.company.domain, page, f.quote, site.urls.get(page))
        f.source_link = local_source_link(site.company.id, page, f.quote)
    return findings


def tier_for(findings: list[Finding], criteria: list[Criterion]) -> tuple[str, float]:
    by_id = {f.criterion_id: f for f in findings}
    total = sum(c.weight for c in criteria) or 1
    got = sum(c.weight * STATUS_CREDIT[by_id[c.id].status] for c in criteria if c.id in by_id)
    score = round(got / total, 2)
    must_fail = any(c.must_have and by_id.get(c.id) and by_id[c.id].status not in ("met", "partial")
                    for c in criteria)
    if must_fail:
        return "C", score
    if score >= TIER_A:
        return "A", score
    if score >= TIER_B:
        return "B", score
    return "C", score


def short_label(f: Finding) -> str:
    if f.criterion_id in ("industry", "size", "region"):
        return f.criterion_id
    return f.label.split(":", 1)[-1].strip()


def summarize(findings: list[Finding]) -> str:
    met = sum(f.status == "met" for f in findings)
    parts = [f"Meets {met} of {len(findings)} criteria."]
    missing = [short_label(f) for f in findings if f.status == "no_evidence"]
    against = [short_label(f) for f in findings if f.status == "not_met"]
    if against:
        parts.append("Against: " + "; ".join(against) + ".")
    if missing:
        parts.append("No evidence: " + "; ".join(missing) + ".")
    return " ".join(parts)


def draft_hook(findings: list[Finding], tier: str) -> str | None:
    if tier == "C":
        return None
    by_id = {f.criterion_id: f for f in findings}
    for sig in ("sig_manual", "sig_compliance", "sig_expansion", "sig_hiring"):
        f = by_id.get(sig)
        if f and f.status == "met" and f.quote:
            return f'DRAFT - review before sending: Your website says "{f.quote}" It sounds like {HOOKS[sig]}'
    return None


def score_company(site: Website, criteria: list[Criterion], scorer: Scorer) -> CompanyResult:
    active = [c for c in criteria if c.enabled]
    findings = enforce_evidence(site, scorer.findings(site, active))
    tier, score = tier_for(findings, active)
    c = site.company
    return CompanyResult(
        id=c.id, name=c.name, domain=c.domain, website=c.website, similarity=c.similarity,
        tier=tier, score=score, summary=summarize(findings), draft_hook=draft_hook(findings, tier),
        scorer=scorer.name, findings=findings,
    )


def get_scorer() -> Scorer:
    cfg = LLMConfig.from_env()
    return LLMScorer(cfg) if cfg else RuleScorer()
