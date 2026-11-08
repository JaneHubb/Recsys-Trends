"""Semantic Scholar enrichment: abstract, citation count and arXiv id for papers with a DOI. Batch lookup (500 per request).

Abstracts stay in the work directory and are never stored in the repository.
"""
from __future__ import annotations

from . import config
from .utils import Http, Report, chunks, log


_warned_no_key = False


def _http() -> Http:
    headers = {"x-api-key": config.S2_API_KEY} if config.S2_API_KEY else {}
    # without a key we share the public pool, so go slower
    return Http(min_interval=1.1 if config.S2_API_KEY else 3.5, headers=headers)


def enrich(papers: list[dict], report: Report, http: Http | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    """Papers with a DOI only. Returns (metadata {id: {arxiv, cites, s2id}}, abstracts {id: text})."""
    http = http or _http()
    with_doi = [p for p in papers if p.get("doi")]
    meta: dict[str, dict] = {}
    abstracts: dict[str, str] = {}
    if not with_doi:
        return meta, abstracts
    global _warned_no_key
    if not config.S2_API_KEY and not _warned_no_key:
        _warned_no_key = True
        report.warn("S2_API_KEY is not set; calling Semantic Scholar slowly (3.5 s apart). Register a key if 429 errors are frequent")
    n_abs = 0
    for chunk in chunks(with_doi, config.S2_BATCH):
        ids = [f"DOI:{p['doi']}" for p in chunk]
        try:
            r = http.post(f"{config.S2_API}/paper/batch", params={"fields": config.S2_FIELDS}, json={"ids": ids}, timeout=120)
            if r.status_code != 200:
                report.warn(f"S2 batch HTTP {r.status_code}: {r.text[:120]}")
                continue
            rows = r.json()
        except Exception as e:  # noqa: BLE001
            report.warn(f"S2 batch failed: {e.__class__.__name__}: {str(e)[:120]}")
            continue
        for p, row in zip(chunk, rows):
            if not row:
                continue
            ext = row.get("externalIds") or {}
            meta[p["id"]] = {
                "s2id": row.get("paperId"),
                "arxiv": ext.get("ArXiv"),
                "cites": row.get("citationCount"),
            }
            if row.get("abstract"):
                abstracts[p["id"]] = row["abstract"]
                n_abs += 1
    log.info("S2: %d looked up, %d abstracts", len(with_doi), n_abs)
    report.info(f"Semantic Scholar: {len(with_doi)} DOIs → metadata for {len(meta)}, abstracts for {n_abs}")
    return meta, abstracts
