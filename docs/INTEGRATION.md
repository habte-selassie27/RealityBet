# RealityBet Integration Guide

Contract: `0x0905C1CE5680AF08354ADF09254AE86fDF66E72D` (StudioNet).
Full command reference: [COMMANDS.md](../COMMANDS.md). Method counts and payable flags:
[proof/schema-summary.json](../proof/schema-summary.json).

## Consumer pattern: read before you write

```bash
C=0x0905C1CE5680AF08354ADF09254AE86fDF66E72D

genlayer call $C get_platform_stats                 # fee_bps, owner, totals
genlayer call $C get_market_ids   --args 0 10       # newest first, cap 50
genlayer call $C get_markets_page --args 0 10       # same ids + full dicts
genlayer call $C get_market       --args "$MID"
genlayer call $C get_odds         --args "$MID"     # implied %, pools, total
```

Ids are `m<seq>-<timestamp>` / `b<seq>-<timestamp>` / `d<seq>-<timestamp>`. Both list
views are newest-first and numerically ordered (`m10` sorts after `m2` — `_seq`,
contracts/RealityBet.py:439), but **an id string itself is not sortable**: parse the
`<seq>` if you sort client-side. `limit` is clamped to 50 (line 452); `offset` is
never negative.

For a page of markets, use `get_markets_page`, not N× `get_market` — it batches full
dicts. For a market's bets use `get_market_bets_detailed` (newest first); for one
address use `get_bets_by_bettor` / `get_bettor_bets` (both accept `0x`-prefixed or bare
hex; the contract normalizes via `_addr_key`, line 107).

## Consumer pattern: place a bet

`place_bet` and `fund_market` are payable, and **the CLI cannot send value** —
`genlayer write` hardcodes `value: 0n`, so those two calls always revert with
`Must send GEN`. Use the Studio UI or genlayer-js:

```js
// npm i genlayer-js viem
import { createClient, chains } from "genlayer-js";
import { privateKeyToAccount } from "viem/accounts";

const client = createClient({
  chain: chains.studionet,
  endpoint: "https://studio.genlayer.com/api",
  account: privateKeyToAccount(process.env.GL_KEY),
});

const hash = await client.writeContract({
  address: "0x0905C1CE5680AF08354ADF09254AE86fDF66E72D",
  functionName: "place_bet",
  args: [marketId, "yes"],          // side: "yes" | "no"
  value: 1_000_000_000_000_000_000n, // 1 GEN, 18 decimals
});
await client.waitForTransactionReceipt({ hash });
```

Rules to enforce client-side *and* expect on-chain: market must be `OPEN`, `now <
close_time`, `value > 0`, side is `yes`/`no` (line 179). Pools update immediately, so
re-read `get_odds` right before submitting — a bet moves the price.

> Do not use `fund_market` for real settlement: it splits value across both pools with
> no bet attached, inflates the parimutuel ratio, and can never be claimed back. It
> exists only to seed non-zero display odds on a fresh market.

## Consumer pattern: follow a market's life

```bash
# after close_time (anyone)
genlayer write $C lock_market        --args "$MID"
# after resolve_time (anyone — runs consensus, costs fees)
genlayer write $C request_resolution --args "$MID"
genlayer call  $C get_market         --args "$MID"   # status/outcome/resolver_*
```

Then, depending on `status`:

- `resolved` + `outcome in {yes,no}` → winners call `claim_winnings <bet_id>` (once;
  reverts `Already claimed`) — **but only after the 24h dispute window closes**:
  `claim_winnings` reverts `Dispute window not closed` while
  `now < resolved_at + 86400`, and `Market under dispute` whenever an appeal is open.
- `resolved` + `outcome == void` → also `claim_winnings`: the void branch pays the full
  stake back with no fee (line 224), under the same window gate.
- `voided` (parse error, or `void_market`) → `refund_void <bet_id>`; that path exists
  only for the `VOIDED` status (line 250) and carries no window — `VOIDED` cannot be
  disputed.
- Within 24h of `resolved_at` (**strictly**: `now < resolved_at + 86400`, line 364) a
  bettor may `raise_dispute <MID> "<reason>"`; status becomes `disputed`, which also
  blocks every claim. Owner then `resolve_dispute <DID> <true|false> <outcome> <note>`
  or `re_resolve <MID>` (re-runs the model). An **upheld** ruling re-stamps
  `resolved_at`, restarting the full window before anything can be claimed.

The contract itself refuses to pay before finality — a resolution is provisional until
`resolved_at + 24h`, claims and disputes are gated on disjoint boundaries of that
instant, and an open dispute blocks claims outright. A UI that pays out or marks
positions final earlier is wrong even when the contract would have accepted the call.

## Consumer pattern: inspect an audit trail

`get_market` returns `resolver_note`, `resolver_confidence`, `resolver_sources`,
`resolved_at` alongside `outcome`/`status`. Note that `resolver_sources` is stored as a
JSON **string**, not a list. Prefixes are meaningful:

| Prefix / note | Meaning |
|---|---|
| `Low confidence auto-void.` | model said yes/no but confidence was low |
| `Invalid AI outcome voided` | model returned an unrecognised outcome |
| `AI parse error voided` | reply was not JSON; market went `voided` |
| `[FORCED]` | owner `force_resolve` |
| `[DISPUTE UPHELD]` / `[DISPUTE REJECTED]` | owner ruling on a dispute |

## Ownership and admin

Owner is `0x04e0353B7218b66D6803725ce7342E6e1225DB1b` (deployer — `__init__` assigns
`self.owner = gl.message.sender_address`, line 87). Owner-gated methods: `force_resolve`,
`resolve_dispute`, `re_resolve`, `set_fee` (≤ 500 bps), `transfer_ownership`. Anyone
else calling them reverts with `Only owner`.

Writes are signed by the active keystore (`genlayer account show`), not by a browser
wallet; only `genlayer account` commands take `--account`. See the signing section of
[COMMANDS.md](../COMMANDS.md).

## Recommended integration invariants

1. **Pin the address and the source.** `genlayer code $C | diff - contracts/RealityBet.py`
   must be clean (the deployed copy has one extra trailing newline; see
   [proof/official-deployment.json](../proof/official-deployment.json)).
2. **Never compute money from model output.** Read pools/`fee_bps` from `get_odds` /
   `get_market`, or replay the formula in [README §5](../README.md#5-money) with
   integers.
3. **Gate claims on `status`, `claimed`, and the dispute window.** The contract enforces
   all three (`RESOLVED` + not `DISPUTED` + `now >= resolved_at + 86400`); your UI
   should not offer a button the chain will reject. Re-read `resolved_at` after any
   dispute ruling — an upheld ruling restarts the window.
4. **Assume reverts.** Every failure is `gl.vm.UserError` with a short message surfaced
   through the CLI as `Market not found`, `Only owner`, `Betting closed`, etc. Match on
   the message, not on an error code.
5. **Budget for the resolution round.** `request_resolution` is a consensus
   transaction that performs web fetches and LLM calls — it is slow and costs fees.
   Don't put it in a polling loop.
