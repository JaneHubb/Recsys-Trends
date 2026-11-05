#!/usr/bin/env bash
# GitHub Issue 생성 또는 기존 열린 Issue 에 댓글 (라벨로 중복 방지)
# 사용: gh_issue.sh <label> <title> <body-file | --failure>
set -uo pipefail
LABEL="$1"; TITLE="$2"; SRC="${3:-}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
BODY="$(mktemp)"
{
  echo "- 워크플로: **${GITHUB_WORKFLOW:-?}** · 실행 로그: $RUN_URL"
  echo
  if [ "$SRC" = "--failure" ]; then
    echo "파이프라인이 실패했습니다. 아래 내용을 Claude 대화에 붙여넣으면 원인을 찾을 수 있습니다."
    echo
    echo '```'
    tail -c 3500 state/last_error.txt 2>/dev/null || echo "(state/last_error.txt 없음 — Actions 로그를 확인하세요)"
    echo '```'
    echo
    echo "### 실행 보고서"
    echo
    cat state/last_report.md 2>/dev/null || true
  else
    cat "$SRC" 2>/dev/null || echo "(내용 없음)"
  fi
} > "$BODY"

gh label create "$LABEL" --color d73a4a --description "자동 생성" 2>/dev/null || true
NUM="$(gh issue list --label "$LABEL" --state open --json number --jq '.[0].number' 2>/dev/null || true)"
if [ -n "$NUM" ]; then
  gh issue comment "$NUM" --body-file "$BODY" && echo "Issue #$NUM 에 댓글"
else
  gh issue create --title "$TITLE" --label "$LABEL" --body-file "$BODY" && echo "Issue 생성"
fi
