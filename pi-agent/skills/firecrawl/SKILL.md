---
name: firecrawl
description: "Use Firecrawl HTTP API for webpage scraping, site mapping, and small crawls when the task needs structured extraction from specific URLs or sites. Requires FIRECRAWL_API_KEY."
metadata:
  api-key-env: FIRECRAWL_API_KEY
  api-key-url: https://www.firecrawl.dev/app/t/hHmKUJExk9E/api-keys
---

# Firecrawl

Use Firecrawl when the user provides specific URLs/sites and needs extracted page content, a URL map, or a bounded crawl. Prefer Pi `web_search`/`web_fetch` for simple lookup or one lightweight public page; use Tavily for open web search without a known site.

## Prerequisite

```bash
test -n "$FIRECRAWL_API_KEY" || echo "Set FIRECRAWL_API_KEY first"
```

Never print the key. Firecrawl API v2 uses:

```text
Authorization: Bearer $FIRECRAWL_API_KEY
https://api.firecrawl.dev/v2/...
```

## Scrape one URL

Use for one page, article, PDF page, docs page, or a small number of URLs handled one at a time.

```bash
export FC_URL='https://example.com/page'
mkdir -p reports/firecrawl
out="reports/firecrawl/$(date +%Y%m%d-%H%M%S)-scrape.json"

curl -sS -X POST 'https://api.firecrawl.dev/v2/scrape' \
  -H "Authorization: Bearer $FIRECRAWL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json, os
print(json.dumps({
  "url": os.environ["FC_URL"],
  "formats": ["markdown"],
  "onlyMainContent": True,
  "removeBase64Images": True,
  "blockAds": True,
  "timeout": 60000,
}, ensure_ascii=False))
PY
)" | tee "$out"
```

Read `data.markdown`, `data.metadata`, and `data.links` from the JSON. Treat `success: true` as successful extraction, not as proof that the content answers the user's question.

## Map a site

Use map before crawling when you need to discover relevant URLs or bound a crawl.

```bash
export FC_URL='https://example.com'
export FC_LIMIT=200
curl -sS -X POST 'https://api.firecrawl.dev/v2/map' \
  -H "Authorization: Bearer $FIRECRAWL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json, os
print(json.dumps({
  "url": os.environ["FC_URL"],
  "sitemap": "include",
  "includeSubdomains": False,
  "ignoreQueryParameters": True,
  "limit": int(os.environ.get("FC_LIMIT", "200")),
  "timeout": 60000,
}, ensure_ascii=False))
PY
)"
```

Use the returned `links` to select pages. Do not crawl an entire domain when a map plus selected scrapes answers the task.

## Bounded crawl

Use crawl only when multiple pages are necessary and the user asked for site-level extraction.

```bash
export FC_URL='https://example.com/docs'
export FC_LIMIT=50
curl -sS -X POST 'https://api.firecrawl.dev/v2/crawl' \
  -H "Authorization: Bearer $FIRECRAWL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json, os
print(json.dumps({
  "url": os.environ["FC_URL"],
  "limit": int(os.environ.get("FC_LIMIT", "50")),
  "sitemap": "include",
  "ignoreQueryParameters": True,
  "allowExternalLinks": False,
  "allowSubdomains": False,
  "scrapeOptions": {
    "formats": ["markdown"],
    "onlyMainContent": True,
    "removeBase64Images": True,
    "timeout": 60000,
  },
}, ensure_ascii=False))
PY
)"
```

The crawl call returns an `id`; poll the crawl status endpoint:

```bash
export FC_CRAWL_ID='paste-crawl-id-here'
curl -sS "https://api.firecrawl.dev/v2/crawl/$FC_CRAWL_ID" \
  -H "Authorization: Bearer $FIRECRAWL_API_KEY" | python3 -m json.tool
```

Keep `limit` small first, inspect results, then expand only if the first batch shows the missing evidence lives deeper.

## Failure interpretation

- 401: missing/invalid `FIRECRAWL_API_KEY`.
- 402/432-style quota errors: plan or credits issue; stop and report the quota boundary.
- 429: rate limit; reduce concurrency, lower crawl size, or wait.
- `success: false`: surface Firecrawl's `code`/`error`; do not silently fall back to stale cached content.

## Output discipline

For user-facing synthesis, cite original `metadata.sourceURL`/URLs, not only the Firecrawl artifact path. Save raw JSON for reproducibility when the extraction affects a final claim.
