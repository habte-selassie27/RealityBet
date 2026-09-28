# RealityBet Architecture

## Design objective

One contract that settles YES/NO prediction markets where the *only* non-deterministic
input is "what does the source say". Everything else — ids, pools, lifecycle, fees,
payouts, disputes — is deterministic integer state, so validators only have to agree on
one field: the outcome string.

The reusable part is the boundary: `_resolve` (contracts/RealityBet.py:262) is where
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
  in a receipt, collision-free within an instance. `_seq` (line 429) parses the counter
  back out so sorting is numeric; lexicographic order would put `m10` before `m2`.
- Both indexes make "bets on this market" and "bets by this address" single lookups.
  The address index normalizes both sides through `_addr_key` (`format(a, "x")`), so
  `0x`-prefixed and bare-hex lookups hit the same key (lines 107, 519).
- Money and time are `u256` everywhere; `_now()` reads the VM wall clock (line 94).

### Layer 2: non-deterministic resolution boundary

`_resolve` builds a fixed prompt (lines 266-276), fetches the market's primary source,
truncates the body to 6000 chars, asks the model, and hands both to
`gl.eq_principle.prompt_comparative` with a field-level rule (line 288). The parsed
result is then normalized by *deterministic* code (lines 291-329) before it touches
storage. See [CONSENSUS.md](CONSENSUS.md).

### Layer 3: client surface

12 `@gl.public.view` methods return plain dicts/lists (`_market_dict`, `_bet_dict`),
never storage objects. `get_markets_page` / `get_market_bets_detailed` batch a page of
ids into one call, both list views go through `_market_ids` (line 436) with `limit`
clamped to 50 (line 442) and `offset >= 0`.

## Lifecycle state machine

```
  OPEN ──close_time──► LOCKED ──resolve_time──► RESOLVED ──► claim_winnings
   │                     │                          │
   │                     │                     [24h window]
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
| `request_resolution` (253) | permissionless, `now >= resolve_time`, status `LOCKED` | a stuck market can always be settled |
| `void_market` (152) | creator or owner, `OPEN`/`LOCKED` | early cancellation only, pre-outcome |
| `raise_dispute` (350) | any bettor *on that market*, `RESOLVED`, `now <= resolved_at + 86400` | skin in the game, bounded window; status flips to `DISPUTED`, which also blocks a second dispute |
| `force_resolve` / `resolve_dispute` / `re_resolve` | owner only | explicit, attributable override path |
| `set_fee` (557) | owner, `bps <= 500` | hard cap, not a policy setting |

## Money path

```
gross = bet × total_pool ÷ win_pool   (u256 integer division)
fee   = gross × fee_bps ÷ 10000       → owner     (line 227, 230)
net   = gross − fee                   → bettor    (line 228, 234)
void  → full stake back, no fee       (line 249)
```

`claim_winnings` (207) and `refund_void` (237) are the only functions that move value
besides `fund_market`'s split (line 164). Payout ordering is checks-effects-interactions
for the bettor leg: `b.claimed = True` and the storage write happen (231-232) *before*
the bettor transfer (234) — with one nuance, the fee transfer at line 230 precedes the
flag. See [THREAT_MODEL.md](THREAT_MODEL.md#threat-double-spend-via-re-entrancy).

## Bounds (cheapness is consensus cost)

| Bound | Value | Where |
|---|---|---|
| page size | 50 | line 442 |
| body fed to the model | 6000 chars | line 280 |
| categories per market | 5, deduped, whitelist of 20 | `create_market` |
| platform fee | 500 bps hard cap (default 150) | line 557, constructor 88 |
| dispute window | 86400 s | line 354 |
| `resolver_note` | 500 chars | line 303 |
| concurrent models per resolution | 1 comparative call | line 288 |

## Why one file

The whole primitive — storage, state machine, resolution, views — is 570 lines with no
imports beyond `json`, `typing`, `datetime`. There is no frontend, wallet layer, or
off-chain service in this repository; [COMMANDS.md](../COMMANDS.md) is the complete
interface. Splitting it would add cross-file invariants for no isolation benefit: the
non-determinism boundary is a function, not a service.
