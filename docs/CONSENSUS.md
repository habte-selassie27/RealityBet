# RealityBet Consensus Model

## The single non-deterministic step

One method, `_resolve` (contracts/RealityBet.py:295), is where the outside world enters.
Every other write method is pure state arithmetic, so validators only ever have to agree
on two strings: the final `outcome` and the `confidence` it was derived from.

```python
def _fetch() -> str:
    web_data = gl.nondet.web.get(url)            # non-deterministic I/O   (312)
    body = web_data.body.decode("utf-8")[:6000]   # bounded                  (313)
    res = gl.nondet.exec_prompt(prompt + "\nPAGE CONTENT:\n" + body)   # (314-318)
    return json.dumps(self._settlement_policy(res), sort_keys=True)   # (319)

raw = gl.eq_principle.prompt_comparative(
    _fetch,
    "`outcome` and `confidence` must be exactly the same. All other fields must be similar",
)                                                 # (321-324)
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

The comparative rule pins **every field that can change settlement** — `outcome` *and*
`confidence`, because the low-confidence policy rewrites `yes`/`no` to `void` — while
letting `reason` and `sources_checked` stay fuzzy. That split is what makes consensus
both strict about money-relevant output and tolerant about prose.

Two layers make the binding exact (preflight checks 05 and 06):

1. **The policy runs before the comparison.** `_fetch` returns
   `self._settlement_policy(res)` (line 319), so the payload the comparator sees already
   has the fail-toward-void rewrites applied. Exact agreement on `outcome` is therefore
   exact agreement on the *final post-policy settlement result*, not on a raw reply that
   deterministic code might later flip.
2. **The rule names both fields.** Even a tie on `outcome` with differing `confidence`
   (e.g. `high` vs `medium`) is a disagreement under the rule, so consensus can never
   pick between two payloads that would store different `resolver_confidence`.

Key-order normalization matters here: `_fetch` returns `json.dumps(..., sort_keys=True)`
(319) so two identical payloads serialize identically before comparison.

## The prompt

Fixed template (lines 299-309): role, the market's question/description, explicit
criteria (`YES if clearly occurred`, `NO if clearly not`, `VOID if ambiguous, source
unavailable, or unanswerable`), the primary source URL, category, a five-step
procedure, and the response schema:

```
Respond ONLY JSON: {"outcome":"yes","confidence":"high","reason":"...","sources_checked":["url1"]}
```

Then `PAGE CONTENT:` + the truncated body is appended (314). The exact string is in
[examples/resolution-prompt.md](../examples/resolution-prompt.md).

## Deterministic post-processing

The whole policy lives in one idempotent helper, `_settlement_policy` (lines 267-293),
which runs **twice**: inside `_fetch` before the comparative check, and again on the
consensus result (line 327) before anything is written. Whatever consensus returns,
`_resolve` runs it through the same code — so a model that answers badly cannot produce
a bad *state transition*, only a void:

| Step | Rule | Line |
|---|---|---|
| 1 | non-dict reply → `outcome = void`, `AI parse error voided` | 272-274 |
| 2 | key aliases: `outcome`/`result`/`verdict`, `confidence`/`conf`, `reason`/`explanation`/`analysis`, `sources_checked`/`sources` | 275-286 |
| 3 | `confidence == low` and outcome isn't void → rewrite to `void`, keep the original reason prefixed `Low confidence auto-void.` | 287-289 |
| 4 | outcome outside `yes/no/void` → rewrite to `void`, `Invalid AI outcome voided` | 290-292 |
| 5 | consensus string is not JSON → `except` (328), market `VOIDED`, `AI parse error voided` | 328-333 |
| 6 | otherwise write `outcome`, `status=RESOLVED`, note/confidence/sources | 334-340 |

Precedence is deliberately "fail toward refund": only step 6 ever settles money, and
steps 1/3/4/5 all land on `void`, which pays every bettor back in full (line 223-224).

## What validators must reproduce

Inside the equivalence check each validator runs its own `_fetch`: its own
`web.get(url)`, its own `exec_prompt`, and its own copy of `_settlement_policy`.
Consequences worth knowing:

- **If the page changes materially between leader and validator fetches** (score flips,
  site goes down mid-round), the two `outcome`s can differ and the rule fails. Consensus
  does not settle; no state changes; the market stays `LOCKED` and can be re-attempted
  later (`re_resolve`) or overridden (`force_resolve`). Failing to agree is strictly
  better than agreeing on a stale page.
- **If the page is unavailable to a validator**, its model sees no evidence, tends
  toward `void`/low confidence, and the same disagreement path fires.
- **Normalization runs on both sides of the comparison**, so a `low` confidence on one
  side and `high` on the other cannot collapse to the same `outcome` on one side only —
  the policy is deterministic, so equal inputs give equal outputs.
- **Deterministic normalization runs again after comparison**, so both sides must have
  produced a parseable reply for step 6 to matter; a parse failure voids the market
  regardless of what the leader saw.

## Failure handling

| Failure | Path | Result |
|---|---|---|
| Unparsable consensus string | `except` (328) | market `VOIDED`, refunds open |
| `confidence: low` | forced pre-consensus (287) | `void` + note, status `RESOLVED` |
| Unknown outcome string | forced pre-consensus (290) | `void` + note |
| Alias keys only | accepted (275-286) | normalized to the canonical fields |
| Validators disagree beyond the rule | consensus round fails | no state change; appeal path |
| Persistent disagreement | owner | `re_resolve` (fresh fetch) or `force_resolve` (explicit override, `[FORCED]` prefix in `resolver_note`, line 355) |

## Trust summary

- Consensus decides **which** of the post-policy results is stored — and it must agree
  on `outcome` *and* `confidence` exactly to store anything at all.
- Deterministic code decides whether that result is allowed to settle money at all, and
  when: `claim_winnings` (208) opens only after the dispute window closes.
- Payout math never reads model output — `claim_winnings` (221-239) computes from pools,
  `fee_bps` and `b.amount` only.
- The audit trail (`resolver_note`, `resolver_confidence`, `resolver_sources`,
  `resolved_at`) is written in the same transaction, so a settled market carries the
  evidence for its own dispute window.
