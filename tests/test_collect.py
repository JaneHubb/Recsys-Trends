"""수집 모듈의 파싱 로직 (네트워크 없이)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from pipeline import collect_dblp, collect_openreview, venues
from pipeline.utils import Report


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses   # url → (status, text|json)
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        key = url if url in self.responses else next((k for k in self.responses if url.startswith(k)), None)
        status, body = self.responses[key]
        if isinstance(body, str):
            return NS(status_code=status, text=body, json=lambda: {}, raise_for_status=lambda: None)
        return NS(status_code=status, text="", json=lambda: body, raise_for_status=lambda: None)


def _hit(key, title, pages, doi="10.1145/x", authors=None, typ="Conference and Workshop Papers"):
    return {"info": {"key": key, "title": title, "pages": pages, "doi": doi, "ee": f"https://doi.org/{doi}", "type": typ,
                     "authors": {"author": authors or [{"text": "Ann Lee"}, {"text": "Bob Kim 0001"}]}, "year": "2024"}}


def test_dblp_collect_with_sections():
    hits = [_hit("conf/sigir/A24", "Full Paper One.", "1-10"), _hit("conf/sigir/B24", "Short One.", "11-14"),
            _hit("conf/sigir/C24", "Keynote.", "15-16"), _hit("conf/sigir/P24", "Proceedings", None, typ="Editorship")]
    api = {"result": {"hits": {"@total": "4", "hit": hits}}}
    html = """<html><body>
    <header class="h2"><h2>Full Papers</h2></header><ul class="publ-list"><li class="entry inproceedings" id="conf/sigir/A24">x</li></ul>
    <header class="h2"><h2>Short Papers</h2></header><ul class="publ-list"><li class="entry inproceedings" id="conf/sigir/B24">x</li></ul>
    <header class="h2"><h2>Keynote Talks</h2></header><ul class="publ-list"><li class="entry inproceedings" id="conf/sigir/C24">x</li></ul>
    </body></html>"""
    http = FakeHttp({"https://dblp.org/search/publ/api": (200, api), "https://dblp.org/db/conf/sigir/sigir2024.html": (200, html)})
    res = collect_dblp.collect(http, venues.get("SIGIR"), 2024, Report("t"))
    by = {p["id"]: p for p in res["papers"]}
    assert len(by) == 3 and res["sections"] and res["volumes"] == ["db/conf/sigir/sigir2024.bht"]
    assert by["dblp:conf/sigir/A24"]["track"] == "main" and by["dblp:conf/sigir/A24"]["title"] == "Full Paper One"
    assert by["dblp:conf/sigir/B24"]["track"] == "short"
    assert by["dblp:conf/sigir/C24"]["track"] == "non_paper"
    assert by["dblp:conf/sigir/A24"]["authors"] == ["Ann Lee", "Bob Kim"]


def test_dblp_sections_blocked_falls_back_to_pages():
    hits = [_hit("conf/cikm/A24", "Long.", "1-10"), _hit("conf/cikm/B24", "Short.", "1-4")]
    api = {"result": {"hits": {"@total": "2", "hit": hits}}}
    http = FakeHttp({"https://dblp.org/search/publ/api": (200, api), "https://dblp.org/db/conf/cikm/cikm2024.html": (403, "<html>Anubis</html>")})
    report = Report("t")
    res = collect_dblp.collect(http, venues.get("CIKM"), 2024, report)
    tracks = {p["id"]: p["track"] for p in res["papers"]}
    assert tracks == {"dblp:conf/cikm/A24": "main", "dblp:conf/cikm/B24": "short"}
    assert not res["sections"] and any("Anubis" in w for w in report.warnings)


def test_dblp_kdd_two_volumes_dedup():
    a = {"result": {"hits": {"@total": "1", "hit": [_hit("conf/kdd/A25", "A.", "1-10")]}}}
    http = FakeHttp({"https://dblp.org/search/publ/api": (200, a), "https://dblp.org/db/conf/kdd/": (403, "")})
    res = collect_dblp.collect(http, venues.get("KDD"), 2025, Report("t"), with_sections=False)
    assert len(res["papers"]) == 1 and len(res["volumes"]) == 3   # 같은 응답을 세 번 받았지만 중복 제거


def test_openreview_v1_decisions_and_v2_badges():
    v1_notes = {"notes": [
        {"id": "n1", "forum": "n1", "content": {"title": "Rec A", "authors": ["X"], "abstract": "abs"},
         "details": {"directReplies": [{"invitation": "ICLR.cc/2023/Conference/Paper1/-/Decision", "content": {"decision": "Accept: notable-top-5%"}}]}},
        {"id": "n2", "forum": "n2", "content": {"title": "Rej", "authors": ["Y"]},
         "details": {"directReplies": [{"invitation": "ICLR.cc/2023/Conference/Paper2/-/Decision", "content": {"decision": "Reject"}}]}},
        {"id": "n3", "forum": "n3", "content": {"title": "NoDecision", "authors": ["Z"]}, "details": {"directReplies": []}},
    ]}
    http = FakeHttp({"https://api.openreview.net/notes": (200, v1_notes)})
    res = collect_openreview.collect(http, venues.get("ICLR"), 2023, venues.openreview_configs(venues.get("ICLR"), 2023), Report("t"))
    assert [p["id"] for p in res["papers"]] == ["or:n1"] and res["papers"][0]["badge"] == "oral"
    assert res["abstracts"] == {"or:n1": "abs"}

    v2_notes = {"notes": [{"id": "m1", "forum": "m1", "content": {"title": {"value": "T"}, "authors": {"value": ["A"]}, "venue": {"value": "ICLR 2025 spotlight"}, "abstract": {"value": "ab"}}}]}
    http = FakeHttp({"https://api2.openreview.net/notes": (200, v2_notes)})
    res = collect_openreview.collect(http, venues.get("ICLR"), 2025, venues.openreview_configs(venues.get("ICLR"), 2025), Report("t"))
    assert res["papers"][0]["badge"] == "spotlight" and res["papers"][0]["url"] == "https://openreview.net/forum?id=m1"


def test_openreview_v1_venueid_style():
    note = {"content": {"venueid": "NeurIPS.cc/2022/Conference", "venue": "NeurIPS 2022 Accept"}}
    assert collect_openreview._accepted_v1(note, "NeurIPS.cc/2022/Conference") == (True, "NeurIPS 2022 Accept")
    note = {"content": {"venueid": "NeurIPS.cc/2022/Conference/Rejected", "venue": "Submitted to NeurIPS 2022"}}
    assert collect_openreview._accepted_v1(note, "NeurIPS.cc/2022/Conference")[0] is False
