# RealityBet Architecture

## Design objective

One contract that settles YES/NO prediction markets where the *only* non-deterministic
input is "what does the source say". Everything else — ids, pools, lifecycle, fees,
payouts, disputes — is deterministic integer state, so validators only have to agree on
the settlement decision: the `outcome` string *and* the `confidence` it was derived from,
both compared exactly after the fail-toward-void policy has been applied.

The reusable part is the boundary: `_resolve` (contracts/RealityBet.py:295) is where
non-determinism enters, and no arithmetic downstream of it ever reads LLM output.

## Layer separation

### Layer 1: deterministic state machine

Storage is five `TreeMap`s declared on the single `RealityBet` class:

```python
markets:     TreeMap[str, Market]         # m<seq>-<ts> → market
bets:        TreeMap[str, Bet]            # b<seq>-<ts> → bet
disputes:    TreeMap[str, Dispute]        # d<seq>-<ts> → dispute
market_bets: TreeMap[str, DynArray[str]]  # market → [bet_id]   (secondary index)
bettor_bets: TreeMap[str, DynArray[str]]  # addr   → [bet_id]   (secondary index)
```

- Ids embed a monotonic counter plus the creation timestamp (`b7-1735689600`) — readable
  in a receipt, collision-free within an instance. `_seq` (line 439) parses the counter
  back out so sorting is numeric; lexicographic order would put `m10` before `m2`.
- Both indexes make "bets on this market" and "bets by this address" single lookups.
  The address index normalizes both sides through `_addr_key` (`format(a, "x")`), so
  `0x`-prefixed and bare-hex lookups hit the same key (lines 107, 530).
- Money and time are `u256` everywhere; `_now()` reads the VM wall clock (line 94).

### Layer 2: non-deterministic resolution boundary

`_resolve` builds a fixed prompt (lines 299-309), fetches the market's primary source,
truncates the body to 6000 chars, asks the model, and normalizes the reply through the
deterministic `_settlement_policy` (lines 267-293) **inside `_fetch`** before handing it
to `gl.eq_principle.prompt_comparative` with a field-level rule (lines 321-324). The
rule binds `outcome` *and* `confidence` — the two fields that change settlement — so
consensus agrees on the final post-policy result, not just the raw verdict. The same
policy runs idempotently again on the consensus output (line 327) before it touches
storage. See [CONSENSUS.md](CONSENSUS.md).

### Layer 3: client surface

12 `@gl.public.view` methods return plain dicts/lists (`_market_dict`, `_bet_dict`),
never storage objects. `get_markets_page` / `get_market_bets_detailed` batch a page of
ids into one call, both list views go through `_market_ids` (line 446) with `limit`
clamped to 50 (line 452) and `offset >= 0`.

## Lifecycle state machine

```
  OPEN ──close_time──► LOCKED ──resolve_time──► RESOLVED ──[24h window]──► claim_winnings
   │                     │                          │
   │                     │                       DISPUTED ──► resolve_dispute (owner)
   │                     │                              └──► re_resolve (owner, re-runs AI)
   │                     │
   └─────────────────────┴──► VOIDED ──► refund_void
```

Guards, and why they are shaped that way:

| Method | Gate | Reason |
|---|---|---|
| `create_market` (111) | `close_time` future, `resolve_time >= close_time`, category whitelist, ≤5 deduped tags | garbage in at creation is free to reject |
| `lock_market` (141) | permissionless, `now >= close_time` | nobody can keep a market open past its deadline |
| `request_resolution` (258) | permissionless, `now >= resolve_time`, status `LOCKED` | a stuck market can always be settled |
| `void_market` (152) | creator or owner, `OPEN`/`LOCKED` | early cancellation only, pre-outcome |
| `claim_winnings` (208) | bettor, `claimed` false, status `RESOLVED` (not `DISPUTED`), `now >= resolved_at + 86400` | no value leaves the pool until the outcome is final and unappealable |
| `raise_dispute` (360) | any bettor *on that market*, `RESOLVED`, `now < resolved_at + 86400` | skin in the game, bounded window; status flips to `DISPUTED`, which also blocks a second dispute **and any claim** |
| `force_resolve` / `resolve_dispute` / `re_resolve` | owner only | explicit, attributable override path |
| `set_fee` (567) | owner, `bps <= 500` | hard cap, not a policy setting |

The claim and dispute boundaries are deliberately **disjoint**: claims open at
`now >= resolved_at + 86400`, disputes close at `now < resolved_at + 86400`. At the
exact boundary second a claim succeeds and a dispute cannot be filed, in either
transaction order — so a payout can never be followed by an appeal that flips the
outcome and makes both sides claimable against the same pool. An upheld ruling
re-stamps `resolved_at` (line 399), restarting a full window before any claim.

## Money path

```
gross = bet × total_pool ÷ win_pool   (u256 integer division)
fee   = gross × fee_bps ÷ 10000       → owner     (line 232, 237)
net   = gross − fee                   → bettor    (line 233, 239)
void  → full stake back, no fee       (line 224)
```

`claim_winnings` (208) and `refund_void` (243) are the only functions that move value
besides `fund_market`'s split (line 164). Payout ordering is checks-effects-interactions
for **every** leg: `b.claimed = True` and the storage write happen (234-235) *before*
the fee transfer (236-237) and the bettor transfer (238-239). See
[THREAT_MODEL.md](THREAT_MODEL.md#threat-double-spend-via-re-entrancy).

## Bounds (cheapness is consensus cost)

| Bound | Value | Where |
|---|---|---|
| page size | 50 | line 452 |
| body fed to the model | 6000 chars | line 313 |
| categories per market | 5, deduped, whitelist of 20 | `create_market` |
| platform fee | 500 bps hard cap (default 150) | line 567, constructor 88 |
| dispute window / claim delay | 86400 s | lines 219, 364 |
| `resolver_note` | 500 chars | line 280 |
| concurrent models per resolution | 1 comparative call | line 321 |

## Why one file

The whole primitive — storage, state machine, resolution, views — is 580 lines with no
imports beyond `json`, `typing`, `datetime`. There is no frontend, wallet layer, or
off-chain service in this repository; [COMMANDS.md](../COMMANDS.md) is the complete
interface. Splitting it would add cross-file invariants for no isolation benefit: the
non-determinism boundary is a function, not a service.
