# RealityBet Consensus Model

## The single non-deterministic step

One method, `_resolve` (contracts/RealityBet.py:262), is where the outside world enters.
Every other write method is pure state arithmetic, so validators only ever have to agree
on a string: `yes`, `no`, or `void`.

```python
def _fetch() -> str:
    web_data = gl.nondet.web.get(url)            # non-deterministic I/O   (279)
    body = web_data.body.decode("utf-8")[:6000]   # bounded                  (280)
    res = gl.nondet.exec_prompt(prompt + "\nPAGE CONTENT:\n" + body)   # (281-282)
    return json.dumps(json.loads(cleaned), sort_keys=True)             # (284-286)

raw = gl.eq_principle.prompt_comparative(
    _fetch, "`outcome` must be exactly the same. All other fields must be similar"
)                                                 # (288-290)
```

`gl.nondet.*` appears nowhere else in the file — an AST assertion in
`scripts/preflight.py` (check 04) enforces exactly two non-deterministic calls, both
inside `_fetch`, plus one `prompt_comparative` inside `_resolve`.

## Why `prompt_comparative` with a field rule

| Alternative | Why not |
|---|---|
| `strict_eq` on the raw reply | Two honest validators can word the same verdict differently, order keys differently, or add a newline. Byte equality on free text fails consensus constantly. |
| `prompt_equivalence` | Compares leader vs validator output but cannot say *which* fields must match — only "these look equivalent". |
| JSON-schema validation | Confirms the reply is well-formed JSON, never whether it is *right*. |
| Trust the leader | Defeats the point: the whole claim is that each validator re-reads the source itself. |

The comparative rule pins the decision field to exact equality while letting `reason`
and `sources_checked` stay fuzzy. That split is what makes consensus both strict about
money-relevant output and tolerant about prose.

Key-order normalization matters here: `_fetch` returns `json.dumps(..., sort_keys=True)`
(284, 286) so two identical payloads serialize identically before comparison.

## The prompt

Fixed template (lines 266-276): role, the market's question/description, explicit
criteria (`YES if clearly occurred`, `NO if clearly not`, `VOID if ambiguous, source
unavailable, or unanswerable`), the primary source URL, category, a five-step
procedure, and the response schema:

```
Respond ONLY JSON: {"outcome":"yes","confidence":"high","reason":"...","sources_checked":["url1"]}
```

Then `PAGE CONTENT:` + the truncated body is appended (281). The exact string is in
[examples/resolution-prompt.md](../examples/resolution-prompt.md).

## Deterministic post-processing

Whatever consensus returns, `_resolve` runs it through the same code (lines 291-329) —
so a model that answers badly cannot produce a bad *state transition*, only a void:

| Step | Rule | Line |
|---|---|---|
| 1 | non-string / non-dict reply → `outcome = void` | 315-317 |
| 2 | key aliases: `outcome`/`result`/`verdict`, `confidence`/`conf`, `reason`/`explanation`/`analysis`, `sources_checked`/`sources` | 298-304 |
| 3 | `confidence == low` and outcome isn't void → rewrite to `void`, keep the original reason prefixed `Low confidence auto-void.` | 309-311 |
| 4 | outcome outside `yes/no/void` → rewrite to `void`, `Invalid AI outcome voided` | 312-314 |
| 5 | `json.loads` raises → market `VOIDED` outright, `AI parse error voided` | 318-323 |
| 6 | otherwise write `outcome`, `status=RESOLVED`, note/confidence/sources | 324-329 |

Precedence is deliberately "fail toward refund": only step 6 ever settles money, and
steps 1/3/4/5 all land on `void`, which pays every bettor back in full (line 218-219).

## What validators must reproduce

Inside the equivalence check each validator runs its own `_fetch`: its own
`web.get(url)` and its own `exec_prompt`. Consequences worth knowing:

- **If the page changes materially between leader and validator fetches** (score flips,
  site goes down mid-round), the two `outcome`s can differ and the rule fails. Consensus
  does not settle; no state changes; the market stays `LOCKED` and can be re-attempted
  later (`re_resolve`) or overridden (`force_resolve`). Failing to agree is strictly
  better than agreeing on a stale page.
- **If the page is unavailable to a validator**, its model sees no evidence, tends
  toward `void`/low confidence, and the same disagreement path fires.
- **Deterministic normalization runs after comparison**, so both sides must have
  produced a parseable reply for step 6 to matter; a parse failure voids the market
  regardless of what the leader saw.

## Failure handling

| Failure | Path | Result |
|---|---|---|
| Unparsable LLM output | `except` (318) | market `VOIDED`, refunds open |
| `confidence: low` | forced (309) | `void` + note, status `RESOLVED` |
| Unknown outcome string | forced (312) | `void` + note |
| Alias keys only | accepted (298-304) | normalized to the canonical fields |
| Validators disagree beyond the rule | consensus round fails | no state change; appeal path |
| Persistent disagreement | owner | `re_resolve` (fresh fetch) or `force_resolve` (explicit override, `[FORCED]` prefix in `resolver_note`, line 345) |

## Trust summary

- Consensus decides **which** of three strings is stored.
- Deterministic code decides whether that string is allowed to settle money at all.
- Payout math never reads model output — `claim_winnings` (208) computes from pools,
  `fee_bps` and `b.amount` only.
- The audit trail (`resolver_note`, `resolver_confidence`, `resolver_sources`,
  `resolved_at`) is written in the same transaction, so a settled market carries the
  evidence for its own dispute window.
