"""DBLP collection: the full table of contents via the search API (JSON), plus section (track) names from the TOC HTML.

- One TOC = one bht key (e.g. db/conf/kdd/kdd2024.bht). All candidates for a venue-year are tried and de-duplicated by paper key.
- Section enrichment is optional. If the DBLP web page is behind bot protection it is skipped and tracks come from the page-count rule.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from . import config, venues
from .utils import Http, Report, log

PAPER_TYPES = {"Conference and Workshop Papers"}


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def toc_keys(vcfg: dict, year: int) -> list[str]:
    stream = vcfg["dblp"].get("stream") or f"conf/{vcfg['id'].lower()}"
    if vcfg["id"] == "NeurIPS":
        stream = "conf/nips"
    return [f"db/{stream}/{p.format(year=year)}.bht" for p in vcfg["dblp"]["tocs"]]


def fetch_toc(http: Http, bht: str) -> list[dict]:
    """All entries of a TOC (raw info dicts); [] if the TOC does not exist."""
    hits: list[dict] = []
    first = 0
    while True:
        r = http.get(config.DBLP_API, params={"q": f"toc:{bht}:", "h": 1000, "f": first, "format": "json"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        res = r.json().get("result", {}).get("hits", {})
        total = int(res.get("@total", 0))
        batch = [h.get("info", {}) for h in _as_list(res.get("hit"))]
        hits.extend(batch)
        first += len(batch)
        if not batch or first >= total or first >= 9000:
            break
    return hits


def fetch_sections(http: Http, bht: str, report: Report) -> dict[str, tuple[str | None, str | None]]:
    """paper key → (h2 section, h3 section) from the TOC HTML; {} on failure."""
    url = f"{config.DBLP_WEB}/{bht[len('db/'):-len('.bht')]}.html"
    try:
        r = http.get(url, headers={"Accept": "text/html"}, retries=2, timeout=60)
    except Exception as e:  # noqa: BLE001
        report.warn(f"DBLP TOC HTML failed ({bht}): {e.__class__.__name__}. Falling back to the page-count rule")
        return {}
    if r.status_code != 200 or "publ-list" not in r.text:
        hint = "looks like bot protection (Anubis)" if ("Anubis" in r.text or r.status_code in (403, 503)) else f"HTTP {r.status_code}"
        report.warn(f"DBLP TOC HTML unavailable ({bht}): {hint}. Falling back to the page-count rule")
        return {}
    soup = BeautifulSoup(r.text, "html.parser")
    out: dict[str, tuple[str | None, str | None]] = {}
    h2 = h3 = None
    for el in soup.find_all(["h2", "h3", "li"]):
        if el.name == "h2":
            h2, h3 = el.get_text(" ", strip=True), None
        elif el.name == "h3":
            h3 = el.get_text(" ", strip=True)
        elif "entry" in (el.get("class") or []) and el.get("id"):
            out[el["id"]] = (h2, h3)
    return out


def _record(info: dict, vid: str, year: int, bht: str) -> dict | None:
    if info.get("type") not in PAPER_TYPES:
        return None
    key = info.get("key")
    if not key:
        return None
    title = (info.get("title") or "").strip()
    title = re.sub(r"\.$", "", title)
    authors = [a.get("text", "") if isinstance(a, dict) else str(a) for a in _as_list((info.get("authors") or {}).get("author"))]
    authors = [re.sub(r"\s+\d{4}$", "", a).strip() for a in authors if a]   # drop the 'Name 0001' disambiguation suffix
    doi = info.get("doi")
    ee = info.get("ee")
    return {
        "id": f"dblp:{key}",
        "venue": vid,
        "year": year,
        "title": title,
        "authors": authors,
        "doi": doi.lower() if doi else None,
        "url": ee if ee else (f"https://doi.org/{doi}" if doi else info.get("url")),
        "pages": info.get("pages"),
        "volume": bht,
    }


def collect(http: Http, vcfg: dict, year: int, report: Report, with_sections: bool = True) -> dict:
    """{'papers': [...], 'volumes': [bht...], 'sections': bool}"""
    vid = vcfg["id"]
    papers: dict[str, dict] = {}
    volumes: list[str] = []
    sections_ok = False
    for bht in toc_keys(vcfg, year):
        hits = fetch_toc(http, bht)
        if not hits:
            log.info("%s %d: %s not found", vid, year, bht)
            continue
        volumes.append(bht)
        secs = fetch_sections(http, bht, report) if with_sections else {}
        if secs:
            sections_ok = True
        n_new = 0
        for info in hits:
            rec = _record(info, vid, year, bht)
            if not rec or rec["id"] in papers:
                continue
            h2, h3 = secs.get(info.get("key"), (None, None))
            track, matched = None, False
            for sec in (h2, h3):
                if sec:
                    track, matched = venues.normalize_track(vcfg, sec, rec["pages"])
                    if matched:
                        break
            if track is None:
                track, _ = venues.normalize_track(vcfg, None, rec["pages"])
            if (h2 or h3) and not matched:
                report.unknown_track(vid, year, h2 or h3 or "?")
            rec["section"] = h2 or h3
            rec["track"] = track
            papers[rec["id"]] = rec
            n_new += 1
        log.info("%s %d: %s → %d papers (sections %s)", vid, year, bht, n_new, "yes" if secs else "no")
    return {"papers": list(papers.values()), "volumes": volumes, "sections": sections_ok}
