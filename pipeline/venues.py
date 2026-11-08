"""venues.yaml loader and track normalization."""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from . import config

NORMAL_TRACKS = ("main", "short", "applied", "resource", "demo", "non_paper")


@lru_cache(maxsize=1)
def load() -> dict:
    with open(config.VENUES_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for vid, v in cfg["venues"].items():
        v["id"] = vid
        v["_rules"] = [(re.compile(r["match"], re.I), r["track"]) for r in v.get("tracks", [])]
        for _, t in v["_rules"]:
            if t not in NORMAL_TRACKS:
                raise ValueError(f"{vid}: unknown track '{t}'")
    return cfg


def venue_ids() -> list[str]:
    return list(load()["venues"].keys())


def get(vid: str) -> dict:
    return load()["venues"][vid]


def counted_tracks() -> list[str]:
    return list(load().get("counted_tracks", ["main", "short", "applied", "resource"]))


def start_year() -> int:
    return int(load().get("start_year", 2018))


def source_for(vcfg: dict, year: int) -> str:
    """Source to use for the given year: dblp | openreview"""
    src = vcfg["source"]
    if src != "mixed":
        return src
    d = vcfg.get("dblp", {}).get("years", {})
    lo, hi = d.get("from", 0), d.get("to", 9999)
    return "dblp" if lo <= year <= hi else "openreview"


def openreview_configs(vcfg: dict, year: int) -> list[dict]:
    out = []
    for c in vcfg.get("openreview", []):
        if c.get("from", 0) <= year <= c.get("to", 9999):
            out.append(c)
    return out


def expected_volumes(vcfg: dict, year: int) -> int:
    n = 1
    for rule in vcfg.get("dblp", {}).get("volumes", []):
        if year >= rule["from"]:
            n = max(n, int(rule["count"]))
    return n


def page_count(pages: str | None) -> int | None:
    """DBLP pages: '1234-1245' → 12, '12:1-12:9' → 9, '7' → 1"""
    if not pages:
        return None
    s = str(pages).strip()
    m = re.match(r"^(?:\d+:)?(\d+)\s*-\s*(?:\d+:)?(\d+)$", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return b - a + 1 if b >= a else None
    if re.match(r"^\d+$", s):
        return 1
    return None


def normalize_track(vcfg: dict, section: str | None, pages: str | None) -> tuple[str, bool]:
    """(normalized track, matched a section rule?). Falls back to the page count when no rule matches."""
    n = page_count(pages)
    if section:
        for rx, track in vcfg["_rules"]:
            if rx.search(section):
                if track == "main" and n is not None and n <= 2:
                    return "non_paper", True          # a 1-2 page talk abstract is not a paper whatever its section
                return track, True
    if n is not None and n <= 2:
        return "non_paper", section is None
    smp = int(vcfg.get("short_max_pages", 0) or 0)
    if smp and n is not None and n <= smp:
        return "short", section is None
    return "main", section is None
