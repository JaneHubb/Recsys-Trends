#!/usr/bin/env bash
# Create a GitHub Issue, or comment on the open one with the same label (no duplicates)
# Usage: gh_issue.sh <label> <title> <body-file | --failure>
set -uo pipefail
LABEL="$1"; TITLE="$2"; SRC="${3:-}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
BODY="$(mktemp)"
{
  echo "- Workflow: **${GITHUB_WORKFLOW:-?}** · run log: $RUN_URL"
  echo
  if [ "$SRC" = "--failure" ]; then
    echo "The pipeline failed. Paste the content below into a Claude conversation to find the cause."
    echo
    echo '```'
    tail -c 3500 state/last_error.txt 2>/dev/null || echo "(no state/last_error.txt — check the Actions log)"
    echo '```'
    echo
    echo "### Run report"
    echo
    cat state/last_report.md 2>/dev/null || true
  else
    cat "$SRC" 2>/dev/null || echo "(empty)"
  fi
} > "$BODY"

gh label create "$LABEL" --color d73a4a --description "auto-generated" 2>/dev/null || true
NUM="$(gh issue list --label "$LABEL" --state open --json number --jq '.[0].number' 2>/dev/null || true)"
if [ -n "$NUM" ]; then
  gh issue comment "$NUM" --body-file "$BODY" && echo "Commented on issue #$NUM"
else
  gh issue create --title "$TITLE" --label "$LABEL" --body-file "$BODY" && echo "Issue created"
fi
