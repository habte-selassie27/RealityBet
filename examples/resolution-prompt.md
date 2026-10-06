# Resolution prompt and LLM response handling

Everything below is produced by `scripts/preflight.py --dump-views`, which runs
`contracts/RealityBet.py` in-process with the web fetch and the LLM mocked. The
prompt is the exact string the contract builds in `_resolve`, including the
truncated page body.

## The prompt the contract sends

```text
You are an impartial prediction market resolver with web access.
MARKET QUESTION: "Will BTC close above $100k on Dec 31 2026?"
DESCRIPTION: Resolves YES if BTC closes above 100k.
CRITERIA: YES if event clearly occurred. NO if clearly NOT occurred. VOID only if ambiguous, source unavailable, or unanswerable.
PRIMARY SOURCE: https://coinmarketcap.com/currencies/bitcoin/
CATEGORY: crypto
1.Fetch primary source.2.Search corroborating sources.3.Decide yes|no|void.4.Confidence high|medium|low.5.Reason max 2 sentences.
Respond ONLY JSON: {"outcome":"yes","confidence":"high","reason":"...","sources_checked":["url1"]}
PAGE CONTENT:
<html>btc 105000</html>
```

`PAGE CONTENT` is `gl.nondet.web.get(url).body.decode()[:6000]` — capped at 6000
characters before it reaches the model.

## How responses are interpreted

The contract parses the JSON defensively: `outcome`/`result`/`verdict`,
`confidence`/`conf`, `reason`/`explanation`/`analysis` are all accepted aliases.
Anything that is not `yes`/`no`/`void` is rewritten to `void`; `confidence: low`
rewrites any non-void outcome to `void`. An unparseable response voids the whole
market (`status: voided`).

The same normalization runs **inside `_fetch`, before the comparative check**, so
validators compare post-policy payloads — the `outcome` and `confidence` they must
agree on exactly is the final settlement result, not the raw model reply. It runs
again on the consensus result (idempotently) before anything is written to storage.

| # | Case | LLM response | Resulting outcome/status | Note |
|---|------|--------------|--------------------------|------|
| 1 | happy path — high confidence settles the market | `{"outcome": "yes", "confidence": "high", "reason": "BTC closed the year above 100k.", "sources_checked": ["https://coinmarketcap.com/currencies/bitcoin/"]}` | `yes` / `resolved` | BTC closed the year above 100k. |
| 2 | low confidence auto-voids even when the model says yes | `{"outcome": "yes", "confidence": "low", "reason": "the page was inconclusive"}` | `void` / `resolved` | Low confidence auto-void. Original: the page was inconclusive |
| 3 | unknown outcome string voids instead of guessing | `{"outcome": "maybe", "confidence": "high", "reason": "not stated"}` | `void` / `resolved` | Invalid AI outcome voided |
| 4 | alias keys (result/conf/explanation) are accepted | `{"result": "no", "conf": "high", "explanation": "the event did not occur"}` | `no` / `resolved` | the event did not occur |
| 5 | unparseable LLM output voids the market outright | `this is not json {{{` | `` / `voided` | AI parse error voided |

## Full resulting market records

```json
[
  {
    "case": "happy path — high confidence settles the market",
    "llm_response": "{\"outcome\": \"yes\", \"confidence\": \"high\", \"reason\": \"BTC closed the year above 100k.\", \"sources_checked\": [\"https://coinmarketcap.com/currencies/bitcoin/\"]}",
    "resulting_market": {
      "categories": [
        "crypto"
      ],
      "category": "crypto",
      "close_time": 1767226600,
      "creator": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "description": "Resolves YES if BTC closes above 100k.",
      "fee_bps": 150,
      "id": "m0-1767225600",
      "outcome": "yes",
      "pool_no": 0,
      "pool_yes": 1000,
      "resolution_url": "https://coinmarketcap.com/currencies/bitcoin/",
      "resolve_time": 1767227600,
      "resolved_at": 1767228000,
      "resolver_confidence": "high",
      "resolver_note": "BTC closed the year above 100k.",
      "resolver_sources": "[\"https://coinmarketcap.com/currencies/bitcoin/\"]",
      "status": "resolved",
      "title": "Will BTC close above $100k on Dec 31 2026?"
    }
  },
  {
    "case": "low confidence auto-voids even when the model says yes",
    "llm_response": "{\"outcome\": \"yes\", \"confidence\": \"low\", \"reason\": \"the page was inconclusive\"}",
    "resulting_market": {
      "categories": [
        "crypto"
      ],
      "category": "crypto",
      "close_time": 1767226600,
      "creator": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "description": "Resolves YES if BTC closes above 100k.",
      "fee_bps": 150,
      "id": "m0-1767225600",
      "outcome": "void",
      "pool_no": 0,
      "pool_yes": 1000,
      "resolution_url": "https://coinmarketcap.com/currencies/bitcoin/",
      "resolve_time": 1767227600,
      "resolved_at": 1767228000,
      "resolver_confidence": "low",
      "resolver_note": "Low confidence auto-void. Original: the page was inconclusive",
      "resolver_sources": "[]",
      "status": "resolved",
      "title": "Will BTC close above $100k on Dec 31 2026?"
    }
  },
  {
    "case": "unknown outcome string voids instead of guessing",
    "llm_response": "{\"outcome\": \"maybe\", \"confidence\": \"high\", \"reason\": \"not stated\"}",
    "resulting_market": {
      "categories": [
        "crypto"
      ],
      "category": "crypto",
      "close_time": 1767226600,
      "creator": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "description": "Resolves YES if BTC closes above 100k.",
      "fee_bps": 150,
      "id": "m0-1767225600",
      "outcome": "void",
      "pool_no": 0,
      "pool_yes": 1000,
      "resolution_url": "https://coinmarketcap.com/currencies/bitcoin/",
      "resolve_time": 1767227600,
      "resolved_at": 1767228000,
      "resolver_confidence": "high",
      "resolver_note": "Invalid AI outcome voided",
      "resolver_sources": "[]",
      "status": "resolved",
      "title": "Will BTC close above $100k on Dec 31 2026?"
    }
  },
  {
    "case": "alias keys (result/conf/explanation) are accepted",
    "llm_response": "{\"result\": \"no\", \"conf\": \"high\", \"explanation\": \"the event did not occur\"}",
    "resulting_market": {
      "categories": [
        "crypto"
      ],
      "category": "crypto",
      "close_time": 1767226600,
      "creator": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "description": "Resolves YES if BTC closes above 100k.",
      "fee_bps": 150,
      "id": "m0-1767225600",
      "outcome": "no",
      "pool_no": 0,
      "pool_yes": 1000,
      "resolution_url": "https://coinmarketcap.com/currencies/bitcoin/",
      "resolve_time": 1767227600,
      "resolved_at": 1767228000,
      "resolver_confidence": "high",
      "resolver_note": "the event did not occur",
      "resolver_sources": "[]",
      "status": "resolved",
      "title": "Will BTC close above $100k on Dec 31 2026?"
    }
  },
  {
    "case": "unparseable LLM output voids the market outright",
    "llm_response": "this is not json {{{",
    "resulting_market": {
      "categories": [
        "crypto"
      ],
      "category": "crypto",
      "close_time": 1767226600,
      "creator": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "description": "Resolves YES if BTC closes above 100k.",
      "fee_bps": 150,
      "id": "m0-1767225600",
      "outcome": "",
      "pool_no": 0,
      "pool_yes": 1000,
      "resolution_url": "https://coinmarketcap.com/currencies/bitcoin/",
      "resolve_time": 1767227600,
      "resolved_at": 0,
      "resolver_confidence": "low",
      "resolver_note": "AI parse error voided",
      "resolver_sources": "",
      "status": "voided",
      "title": "Will BTC close above $100k on Dec 31 2026?"
    }
  }
]
```
