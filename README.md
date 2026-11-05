# RecSys Trends

AI 탑티어 학회(RecSys · SIGIR · KDD · WWW · WSDM · CIKM · NeurIPS · ICLR)의 추천시스템 논문을 학회·연도별로 한눈에 보는 GitHub Pages 사이트입니다. 수집과 판별은 GitHub Actions 가 자동으로 하고, 운영자는 처음 한 번만 설정합니다.

**이 버전은 1단계(뼈대)입니다.** 수집 → LLM 전수 판별 → ⓪ 한눈에 보기 · ① 편수 추이 · ④ 논문 리스트. 4축 태그·한 줄 요약·트렌드 라벨(2단계)과 주목 논문(3단계)은 다음 단계에서 추가합니다.

---

## 1. 처음 설정 (한 번만)

### 1-1. 키 준비

| 키 | 어디서 | 필수 여부 |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | 필수 (판별용) |
| `S2_API_KEY` | [Semantic Scholar API](https://www.semanticscholar.org/product/api) 에서 신청 (승인에 며칠 걸릴 수 있음) | 사실상 필수. 없으면 느리게 돌고 429 오류가 잦음 |

Claude 콘솔에서 **워크스페이스 월 사용 한도를 10달러**로 설정해 두세요 (Settings → Limits). 파이프라인 자체에도 실행당 9.5달러 상한이 있지만, 콘솔 한도가 최종 안전장치입니다.

### 1-2. 레포 만들기

1. GitHub 에서 새 **public** 레포를 만듭니다 (이름 예: `recsys-trends`). Private 도 되지만 GitHub Pages 는 유료 플랜이 필요합니다.
2. 이 zip 의 내용을 레포에 올립니다.
   - **웹에서 올릴 때 주의**: 숨김 폴더 `.github/workflows` 가 빠지기 쉽습니다. 올린 뒤 레포에 `.github/workflows/update.yml` 이 보이는지 꼭 확인하세요. GitHub Desktop 이나 `git push` 를 쓰면 안전합니다.
3. **Settings → Pages → Build and deployment → Source** 를 **GitHub Actions** 로 바꿉니다.
4. **Settings → Secrets and variables → Actions → New repository secret** 에 `ANTHROPIC_API_KEY`, `S2_API_KEY` 를 등록합니다.
5. (선택) 같은 화면의 **Variables** 탭에 `MAX_RUN_USD` 를 만들면 실행당 비용 상한을 바꿀 수 있습니다 (기본 9.5).

### 1-3. 첫 실행

1. **Actions → update → Run workflow** 에서 `dry_run` 을 켜고 실행합니다. 수집만 하고 LLM 은 쓰지 않습니다. 10~20분 걸립니다.
2. 실행이 끝나면 요약(Summary) 화면에 학회·연도별 수집 편수와 **예상 비용**이 나옵니다. 0편인 학회가 있거나 경고가 많으면 그 내용을 Claude 대화에 붙여넣어 `venues.yaml` 을 고칩니다.
3. 문제가 없으면 `dry_run` 을 끄고 다시 실행합니다. 2018년부터 전부 판별하므로(약 5~6만 편) **Batch 처리에 1~3시간**, 비용은 약 **7~9달러**입니다. 상한을 넘으면 최신 연도부터 처리하고 나머지는 미룹니다 (요약에 표시).
4. 끝나면 `https://<계정>.github.io/<레포>/` 에서 사이트를 확인합니다.

실패하면 자동으로 Issue 가 만들어집니다. Issue 본문을 Claude 대화에 붙여넣으면 됩니다.

---

## 2. 그 다음은 자동

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| **update** | 매년 5월 20일, 11월 20일 (10:17 KST) | 집계가 끝나지 않은 학회·연도 수집 → Haiku 전수 판별 → 저확신 논문 Sonnet 재판정 → 집계 → 배포 |
| **advance** | 5·6·11·12월은 4시간마다, 그 외 하루 1회 | update 가 제출한 Batch 가 아직 처리 중이면 이어받아 끝까지 진행. 할 일이 없으면 몇 초 만에 끝남 |
| **monthly** | 매월 1일 | heartbeat 커밋(예약 실행이 60일 비활성으로 꺼지는 것 방지) · 소스 응답 점검 · 재집계 · 배포 |

- 11월 실행에서 그해 7/8 학회(CIKM 제외)가 **잠정** 집계되고, 5월 실행에서 전년이 **확정**됩니다.
- 수동으로 돌리고 싶으면 Actions 탭에서 `Run workflow` 를 누르면 됩니다. CIKM 이 DBLP 에 올라온 걸 알았을 때 `venues: CIKM`, `years: 2026` 으로 돌리는 식입니다.

---

## 3. 비용

Batch API 기준 (Haiku 5.5 입력 0.05 / 출력 0.25 달러, Sonnet 5.5 입력 1 / 출력 5 달러, 100만 토큰당).

| 실행 | 추정 |
|---|---|
| 첫 백필 (2018~현재) | 7~9달러 |
| 정기 업데이트 (연 2회) | 1~3달러 |
| 전체 재판별 (프롬프트·모델 변경 시) | 5~7달러 |

안전장치: 실행 전 비용 추정(9.5달러 상한, 넘으면 최신 연도부터) · 논문당 1회만 처리하고 결과 캐시 · 콘솔 월 한도.

---

## 4. 파일 구조

```
venues.yaml              학회 정의: DBLP 목차 이름, 트랙 규칙, 개최월, OpenReview 설정  ← 가장 자주 고치는 파일
prompts/judge_v1.md      판별 프롬프트 (core / related / no)
pipeline/                파이썬 코드
  run.py                 CLI 진입점 (update · advance · monthly · aggregate · collect)
  collect_dblp.py        DBLP 검색 API + 목차 HTML(섹션 → 트랙)
  collect_openreview.py  OpenReview v1·v2
  enrich_s2.py           Semantic Scholar (초록·인용·arXiv)
  judge.py               Claude Batch 판별
  aggregate.py           집계 상태 판정 · summary.json · papers/*.json · 배포 전 검증
  state.py               상태 머신 (Batch 가 길어져도 다음 실행이 이어받음)
site/                    정적 사이트 (HTML·CSS·JS, 빌드 없음)
data/
  raw/{학회}-{연도}.json  수집 원본 (제목·저자·링크·트랙. 초록은 저장하지 않음)
  llm/judge.jsonl        Haiku 판정 캐시 · rejudge.jsonl  Sonnet 재판정
  summary.json           사이트 첫 화면용 집계
  status.json            학회·연도별 집계 상태
  papers/{학회}-{연도}.json  추천 논문(core·related) + 경계 사례 + demo
  quality.json           Haiku ↔ Sonnet 표본 일치율 (내부용, 사이트에 표시하지 않음)
  changelog.json · meta.json · heartbeat.json · health.json
state/pipeline.json      상태 머신 · last_report.md  최근 실행 보고서
scripts/                 사이트 조립, Issue 생성, 샘플 데이터
tests/                   pytest (외부 API 없이 돈다)
```

---

## 5. 설정 바꾸기

- **학회 추가**: `venues.yaml` 에 항목 하나를 추가합니다. `color` 는 1~8 중 비어 있는 번호 (9개째부터는 사이트에서 회색).
- **트랙 규칙**: DBLP 목차의 섹션 이름이 규칙에 맞지 않으면 `unknown-track` Issue 가 생깁니다. Issue 내용을 Claude 에 붙여넣어 규칙을 추가하면 다음 실행부터 반영됩니다. 그 전까지는 쪽수 규칙으로 main/short 로 셉니다.
- **프롬프트 수정**: `prompts/judge_v2.md` 를 만들고 `pipeline/config.py` 의 `PROMPT_VERSION` 을 올립니다. 다음 실행에서 전체가 다시 판별됩니다 (모델이 섞이면 가짜 추세가 생기므로 일부만 바꾸지 않습니다).
- **모델 변경**: `config.py` 의 `JUDGE_MODEL`·`REJUDGE_MODEL` 과 `PRICES` 를 함께 고칩니다. `update` 를 `force` 로 돌려 전체 재판별합니다.

---

## 6. 집계 규칙 (요약)

- **1편**: DBLP 목차·OpenReview 의 논문 중 `main · short · applied · resource` 트랙. demo 는 토글, keynote·tutorial·workshop·발표 초록(2쪽 이하)은 제외.
- **추천 논문**: LLM 이 `core`(추천 과제를 직접 다룸) / `related`(광고 CTR·검색 랭킹처럼 겹치는 분야) / `no` 로 판별. 통계는 `core` 기본, `related` 는 토글. RecSys 학회는 전편 `core`.
- **집계 완료**: 필요한 볼륨이 다 있고(KDD 는 2025년부터 2개), 편수가 전년의 70% 이상이며, 개최월 + 1개월이 지났을 때(OpenReview 학회는 결정 공개 후 목록이 있으면).
- **기준 연도**: 완료 학회가 6개 이상인 가장 최근 연도. 8/8 이면 확정, 아니면 잠정. 전년 비교는 두 해 모두 완료된 학회끼리.
- **배포 중단**: 완료된 학회·연도의 전체 편수가 20% 넘게 바뀌거나 전체가 50% 넘게 줄면 배포하지 않고 Issue. 원인 확인 후 `skip_validation` 으로 수동 실행.

---

## 7. 로컬에서 미리 보기 (선택)

```bash
pip install -r requirements.txt
python -m pytest -q                      # 테스트
python scripts/make_sample.py            # 가상 데이터로 집계 (API·LLM 불필요)
bash scripts/build_site.sh sample        # _site/ 조립
python -m http.server -d _site 8000      # http://localhost:8000
```

실제 데이터로 돌리려면 환경변수 `ANTHROPIC_API_KEY`, `S2_API_KEY` 를 두고 `python -m pipeline.run update --years 2025 --venues WSDM --dry-run` 처럼 작은 범위부터 시도하세요.

---

## 8. 문제가 생기면

1. 실패하면 `pipeline-failure` 라벨의 Issue 가 자동으로 생기고, 같은 Issue 에 댓글이 쌓입니다. Issue 본문(오류 + 실행 보고서)을 Claude 대화에 붙여넣으세요.
2. Actions 탭 → 해당 실행 → Summary 에도 실행 보고서가 있습니다.
3. Batch 가 24시간 넘게 끝나지 않으면 `state/pipeline.json` 의 `phase` 를 `idle` 로 바꾸고 커밋한 뒤 `update` 를 다시 돌립니다 (제출된 Batch 는 24시간 뒤 자동 만료).

---

## 9. 출처와 라이선스

- 논문 목록: [DBLP](https://dblp.org) (CC0) · [OpenReview](https://openreview.net)
- 초록·인용수·arXiv ID: [Semantic Scholar API](https://api.semanticscholar.org) (초록은 레포에 저장하지 않음)
- 판별: Claude (Anthropic). 오류가 있을 수 있으며, 경계 사례는 사이트에서 토글로 확인할 수 있습니다.
