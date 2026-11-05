"""경로·상수·환경변수. 다른 모듈은 여기서만 설정을 읽는다."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("RECSYS_DATA_DIR", ROOT / "data"))
STATE_DIR = Path(os.environ.get("RECSYS_STATE_DIR", ROOT / "state"))
WORK_DIR = Path(os.environ.get("RECSYS_WORK_DIR", ROOT / "work"))   # 초록 등 임시 파일. 레포에 올리지 않는다
PROMPTS_DIR = ROOT / "prompts"
VENUES_FILE = ROOT / "venues.yaml"

# LLM
PROMPT_VERSION = "v1"                       # prompts/judge_{PROMPT_VERSION}.md
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-haiku-5-5")
REJUDGE_MODEL = os.environ.get("REJUDGE_MODEL", "claude-sonnet-5-5")
MAX_RUN_USD = float(os.environ.get("MAX_RUN_USD", "9.5"))     # 실행 1회 LLM 비용 상한 (추정치 기준)
QUALITY_SAMPLE = int(os.environ.get("QUALITY_SAMPLE", "300"))  # Sonnet 품질 표본 수 (결과는 data/quality.json에만 저장)
BATCH_CHUNK = 8000                           # Batch 1건당 요청 수 (256MB 제한 대비)
JUDGE_MAX_TOKENS = 160
ABSTRACT_MAX_CHARS = 2500

# Batch API 단가 (USD / 100만 토큰). 모델을 바꾸면 여기도 고친다
PRICES = {
    "claude-haiku-5-5": {"in": 0.05, "out": 0.25},
    "claude-sonnet-5-5": {"in": 1.00, "out": 5.00},
}
CHARS_PER_TOKEN = 3.0                        # 보수적 추정 (새 토크나이저는 이전보다 토큰이 많이 나온다)

# 수집
USER_AGENT = os.environ.get(
    "RECSYS_USER_AGENT",
    "recsys-trends/0.1 (GitHub Actions; research use; contact via repository issues)",
)
S2_API_KEY = os.environ.get("S2_API_KEY", "")
S2_FIELDS = "paperId,abstract,citationCount,externalIds"
S2_BATCH = 500
DBLP_API = "https://dblp.org/search/publ/api"
DBLP_WEB = "https://dblp.org/db"
OPENREVIEW_V1 = "https://api.openreview.net"
OPENREVIEW_V2 = "https://api2.openreview.net"
S2_API = "https://api.semanticscholar.org/graph/v1"

# 업데이트 일정 (월, 일)
UPDATE_DATES = [(5, 20), (11, 20)]

# 추천 관련 키워드 (경계 사례 판정용: 키워드는 있는데 LLM이 '아님'으로 본 논문을 따로 보여준다)
KEYWORD_PATTERN = (
    r"recommend|recsys|collaborative filtering|click[- ]?through|\bctr\b|personali[sz]|"
    r"user model|implicit feedback|next[- ]item|session[- ]based|matrix factori|user[- ]item|"
    r"cold[- ]start|user preference|preference learning|bandit|user interest|interest model"
)
