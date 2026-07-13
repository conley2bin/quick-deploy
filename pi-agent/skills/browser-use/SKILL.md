---
name: browser-use
description: "Use Browser Use Cloud API v3 for managed browser automation on interactive websites, login flows, forms, and tasks requiring human-like browser actions. Requires BROWSER_USE_API_KEY."
---

# Browser Use Cloud

Use Browser Use when the task requires a real managed browser: clicking through UI, login/profile state, forms, dynamic pages, downloads, or sites that cannot be handled by `web_fetch`/Firecrawl.

Do not use it for ordinary page extraction, public documentation lookup, or simple search. Use Firecrawl for known-page extraction and Tavily/web_search for source discovery.

## Prerequisite

```bash
test -n "$BROWSER_USE_API_KEY" || echo "Set BROWSER_USE_API_KEY first"
```

Browser Use Cloud API v3 uses:

```text
Base URL: https://api.browser-use.com/api/v3
Header: X-Browser-Use-API-Key: $BROWSER_USE_API_KEY
```

Keys start with `bu_` and can be created at `https://cloud.browser-use.com/settings`.

## Run a browser task

Create a session with a natural-language task. Write the task as an operator instruction with a concrete success condition.

```bash
export BU_TASK='Open example.com and summarize the visible homepage headline and primary CTA.'

curl -sS -X POST 'https://api.browser-use.com/api/v3/sessions' \
  -H "X-Browser-Use-API-Key: $BROWSER_USE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$(python3 - <<'PY'
import json, os
payload = {
  "task": os.environ["BU_TASK"],
  "keepAlive": os.environ.get("BU_KEEP_ALIVE", "false").lower() == "true",
}
if os.environ.get("BU_SESSION_ID"):
  payload["sessionId"] = os.environ["BU_SESSION_ID"]
if os.environ.get("BU_PROFILE_ID"):
  payload["profileId"] = os.environ["BU_PROFILE_ID"]
print(json.dumps(payload, ensure_ascii=False))
PY
)" | tee /tmp/browser-use-session.json
```

Extract the session id from the response field `id`:

```bash
cat /tmp/browser-use-session.json | python3 -m json.tool
```

## Poll for result

```bash
export BU_SESSION_ID='paste-session-id-here'

curl -sS "https://api.browser-use.com/api/v3/sessions/$BU_SESSION_ID" \
  -H "X-Browser-Use-API-Key: $BROWSER_USE_API_KEY" | python3 -m json.tool
```

If the session has message history rather than a compact result, inspect messages:

```bash
curl -sS "https://api.browser-use.com/api/v3/sessions/$BU_SESSION_ID/messages?limit=50" \
  -H "X-Browser-Use-API-Key: $BROWSER_USE_API_KEY" | python3 -m json.tool
```

For follow-up work in the same browser, create another task with `BU_SESSION_ID` and set `keepAlive: true` on the original task when persistent state is needed.

## Profiles and login state

Use a Browser Use profile only when the user wants persistent login state or repeated work on the same site. If a login step, payment, destructive action, or account mutation is involved, get explicit user confirmation for the exact action before submitting the task.

Useful profile discovery:

```bash
curl -sS 'https://api.browser-use.com/api/v3/profiles?pageSize=20&pageNumber=1' \
  -H "X-Browser-Use-API-Key: $BROWSER_USE_API_KEY" | python3 -m json.tool
```

## Browser task prompt pattern

Good tasks include:

```text
Goal: ...
Site/account context: ...
Steps allowed: ...
Do not: submit payments, change account settings, or send messages unless explicitly instructed.
Success condition: return ...
If blocked by login/2FA/CAPTCHA, stop and report exactly what is needed.
```

## Failure interpretation

- 401/403: missing or invalid `BROWSER_USE_API_KEY`.
- 402/account errors: account/credits/subscription issue.
- 429: too many active sessions or rate limit; stop old sessions or wait.
- Session stalls at login/2FA/CAPTCHA: ask the user for the needed intervention rather than guessing credentials.
- Wrong result from dynamic site: retrieve session messages and, if available, live/recording links to inspect what the browser actually saw.

## Evidence discipline

Report what Browser Use observed or changed, not just that the task completed. For extracted data, include source URL/page context and save raw session JSON/messages when the result drives a user-facing claim.
