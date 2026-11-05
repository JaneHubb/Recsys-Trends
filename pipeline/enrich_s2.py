"""Semantic Scholar 보강: DOI가 있는 논문의 초록·인용수·arXiv ID. 일괄 조회(요청당 500편).

초록은 work 디렉터리에만 두고 레포에는 저장하지 않는다.
"""
from __future__ import annotations

from . import config
from .utils import Http, Report, chunks, log


_warned_no_key = False


def _http() -> Http:
    headers = {"x-api-key": config.S2_API_KEY} if config.S2_API_KEY else {}
    # 키가 없으면 공유 풀이라 더 천천히
    return Http(min_interval=1.1 if config.S2_API_KEY else 3.5, headers=headers)


def enrich(papers: list[dict], report: Report, http: Http | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    """DOI가 있는 논문만. (메타데이터 {id: {arxiv, cites, s2id}}, 초록 {id: text})"""
    http = http or _http()
    with_doi = [p for p in papers if p.get("doi")]
    meta: dict[str, dict] = {}
    abstracts: dict[str, str] = {}
    if not with_doi:
        return meta, abstracts
    global _warned_no_key
    if not config.S2_API_KEY and not _warned_no_key:
        _warned_no_key = True
        report.warn("S2_API_KEY 가 없어 Semantic Scholar 를 느리게(3.5초 간격) 호출합니다. 429 오류가 잦으면 키를 등록하세요")
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
            report.warn(f"S2 batch 실패: {e.__class__.__name__}: {str(e)[:120]}")
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
    log.info("S2: %d편 조회, 초록 %d편", len(with_doi), n_abs)
    report.info(f"Semantic Scholar: DOI {len(with_doi)}편 중 메타 {len(meta)}편, 초록 {n_abs}편")
    return meta, abstracts
