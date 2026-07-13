---
name: deep-research
description: "Use for outsourced long-form deep research reports through a deployed u14app/deep-research service, or for manual evidence-led deep research when no service is configured. Trigger on requests for comprehensive research reports, literature/market/technical surveys, and source-backed synthesis."
---

# Deep Research

Use this skill when the user wants a researched report or source-backed synthesis, not a quick factual lookup.

## Choose the mode

- **Service mode**: use a deployed `u14app/deep-research` HTTP service when `DEEP_RESEARCH_BASE_URL` is set or the user asks to use the deep-research service.
- **Manual mode**: use Pi-native research (`web_search`, `web_fetch`, Context7 for library docs, memory/evidence notes) when the service is unavailable, the question needs close agent judgment, or the user wants interactive investigation rather than an outsourced report.

Do not use the service for single facts, one-link lookups, small API questions, or answers that need immediate local code inspection.

## Service prerequisites

Expected environment variables:

```bash
DEEP_RESEARCH_BASE_URL=http://127.0.0.1:3333
DEEP_RESEARCH_ACCESS_PASSWORD=...   # optional; omit header if service has no ACCESS_PASSWORD
```

The service itself must be deployed with its model/search environment configured, for example `GOOGLE_GENERATIVE_AI_API_KEY`, `TAVILY_API_KEY` or `FIRECRAWL_API_KEY`, and provider/model settings required by `u14app/deep-research`.

Quick health check:

```bash
curl -fsS "$DEEP_RESEARCH_BASE_URL" >/dev/null && echo "deep-research reachable"
```

## Run a service report

Create an artifact directory in the current project, then stream the SSE output to a raw evidence file. Fill the JSON deliberately instead of relying on defaults:

```bash
mkdir -p reports/deep-research
out="reports/deep-research/$(date +%Y%m%d-%H%M%S)-deep-research.sse"

headers=(-H "Content-Type: application/json")
if [ -n "${DEEP_RESEARCH_ACCESS_PASSWORD:-}" ]; then
  headers+=(-H "Authorization: Bearer $DEEP_RESEARCH_ACCESS_PASSWORD")
fi

curl -N -sS -X POST "$DEEP_RESEARCH_BASE_URL/api/sse" \
  "${headers[@]}" \
  -d "$(python3 - <<'PY'
import json, os
payload = {
  "query": os.environ.get("DR_QUERY", "研究主题写在这里"),
  "provider": os.environ.get("DR_PROVIDER", "google"),
  "thinkingModel": os.environ.get("DR_THINKING_MODEL", "gemini-2.0-flash-thinking-exp"),
  "taskModel": os.environ.get("DR_TASK_MODEL", "gemini-2.0-flash-exp"),
  "searchProvider": os.environ.get("DR_SEARCH_PROVIDER", "tavily"),
  "language": os.environ.get("DR_LANGUAGE", "zh-CN"),
  "maxResult": int(os.environ.get("DR_MAX_RESULT", "5")),
  "enableCitationImage": False,
  "enableReferences": True,
}
print(json.dumps(payload, ensure_ascii=False))
PY
)" | tee "$out"
```

For a specific query, set variables before the command:

```bash
export DR_QUERY='AI 浏览器自动化产品格局与技术路线'
export DR_PROVIDER=google
export DR_THINKING_MODEL=gemini-2.0-flash-thinking-exp
export DR_TASK_MODEL=gemini-2.0-flash-exp
export DR_SEARCH_PROVIDER=tavily
export DR_LANGUAGE=zh-CN
export DR_MAX_RESULT=8
```

After the stream finishes, inspect the raw SSE file and extract the final Markdown/report text into a sibling `.md` file. Preserve the raw `.sse` file as evidence; do not delete it after summarizing.

## Failure interpretation

- Connection refused / DNS failure: `DEEP_RESEARCH_BASE_URL` is wrong or service is not running.
- 401/403: `DEEP_RESEARCH_ACCESS_PASSWORD` does not match service `ACCESS_PASSWORD`.
- Model/search errors inside the stream: the service is reachable but its provider/search keys or model names are invalid.
- Long silence: deep research is slow by design; if curl is still connected, wait unless the user asked for an early stop.

## Manual mode discipline

When not using the service, run research as evidence-led synthesis:

1. State the mode: lookup, synthesis, or exploratory.
2. Define scope, timeframe, geography, audience, and success criteria when missing.
3. Search authoritative sources first; fetch primary pages; keep contradictions alive.
4. For nontrivial work, create `.memory/tasks/YYYY-MM-DD-topic/{PROMPT.md,OPS.md,EPISTEMIC.md}` when the project uses `.memory/`.
5. Final answers cite sources inline, distinguish observation from interpretation, and state what evidence would change the conclusion.
