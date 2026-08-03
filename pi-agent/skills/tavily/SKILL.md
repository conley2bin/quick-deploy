---
name: tavily
description: "Use Tavily Search HTTP API for current public web search and retrieval when Pi's built-in search is insufficient or the user requests Tavily. Requires TAVILY_API_KEY."
metadata:
  api-key-env: TAVILY_API_KEY
  api-key-url: https://app.tavily.com/home
---

# Tavily

Use Tavily for current public web discovery: recent topics, market/competitor scans, finding candidate sources, or search results where snippets and ranked URLs are enough to choose what to fetch next.

Prefer:
- Context7 for library/framework/API documentation.
- Firecrawl for extracting known URLs or mapping/crawling a specific site.

## Prerequisite

```bash
test -n "$TAVILY_API_KEY" || echo "Set TAVILY_API_KEY first"
```

Tavily Search endpoint:

```text
POST https://api.tavily.com/search
Authorization: Bearer $TAVILY_API_KEY
```

## Basic search

Use `basic` by default; it costs less and is enough for source discovery.

```bash
export TV_QUERY='your search query'
export TV_MAX_RESULTS=5

curl -sS -X POST 'https://api.tavily.com/search' \
  -H "Authorization: Bearer $TAVILY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json, os
print(json.dumps({
  "query": os.environ["TV_QUERY"],
  "search_depth": os.environ.get("TV_SEARCH_DEPTH", "basic"),
  "max_results": int(os.environ.get("TV_MAX_RESULTS", "5")),
  "topic": os.environ.get("TV_TOPIC", "general"),
  "include_answer": False,
  "include_raw_content": False,
  "include_images": False,
  "include_favicon": True,
  "include_usage": True,
}, ensure_ascii=False))
PY
)"
```

Use returned `results[].url`, `title`, `content`, and `score` as discovery evidence. Fetch decisive sources with `web_fetch` or Firecrawl before making strong claims.

## Higher-recall search

Use advanced only when source discovery quality matters more than latency/cost:

```bash
export TV_QUERY='specific hard query'
export TV_SEARCH_DEPTH=advanced
export TV_MAX_RESULTS=10
```

Advanced search can cost more credits. Keep `max_results <= 10` unless the user explicitly asks for broad collection.

## News/current events

For news-like topics, set:

```bash
export TV_TOPIC=news
export TV_TIME_RANGE=week
```

Add `time_range`, `start_date`, or `end_date` only when the time window is part of the question. If date scope matters, state it in the answer.

## Domain filters

When the user names preferred or excluded domains, include filters in the JSON:

```json
"include_domains": ["example.com"],
"exclude_domains": ["pinterest.com"]
```

Use filters to test specific hypotheses, not to hide inconvenient counterevidence.

## Failure interpretation

- 401: missing/invalid `TAVILY_API_KEY`.
- 400: malformed query or invalid `topic`/date/filter parameter.
- 429: rate limiting; reduce calls and batch queries deliberately.
- 432/433: plan or pay-as-you-go limit; report the quota boundary.

## Evidence discipline

Tavily search results are source discovery, not final evidence. For final answers, cite the original pages you fetched/read. If the Tavily-generated `answer` is enabled for a quick check, treat it as a hypothesis to verify against sources.
