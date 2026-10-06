# RealityBet Threat Model

## Protected assets

1. **Pooled stakes** — every GEN in `pool_yes`/`pool_no` must leave the contract exactly
   once: as a payout, a refund, or not at all (dust).
2. **Resolution integrity** — `outcome` must reflect the source, not the model's mood,
   the prompt's attacker, or the leader's byte string.
3. **Payout arithmetic** — `gross`, `fee`, `net` are integer functions of pools and
   `fee_bps`, never of model output.
4. **Owner authority** — `force_resolve`, `set_fee`, `transfer_ownership` must stay with
   the deployer.
5. **Liveness** — a market must always be able to move off a deadline, including when
   nobody is paying attention.

## Threat: model output reaching money

**Vector.** The LLM returns JSON; if any of it were used as an amount, a coerced or
hallucinated number could drain pools.

**Mitigation.** `_resolve` extracts only `outcome`, `confidence`, `reason` (≤500 chars),
`sources` — all strings, written to `resolver_*` fields (`_settlement_policy`,
lines 267-293; storage write 334-339). The payout path
(`claim_winnings`, lines 221-239) reads only `b.amount`, `m.pool_*`, `m.fee_bps`,
`m.outcome`. `scripts/preflight.py` check 08 asserts every `emit_transfer(value=...)`
in the file is `fee`, `payout`, or the original stake.

**Residual.** None found: there is no path from a model string to a transfer amount.

## Threat: malformed, missing, or adversarial model output

**Vector.** Reply is not JSON, has the wrong shape, invents an outcome, or hedges.

**Mitigation.** Deterministic normalization (lines 267-293) fails toward `void`:
non-dict reply → `void` payload (272-274); unknown outcome → `void` (290-292);
`confidence: low` → `void` (287-289); consensus string that is not JSON → market
`VOIDED` (328-333). `void` refunds every stake in full (line 223-224).

**Residual.** A *confidently wrong* high-confidence answer is not detectable here —
that is what the dispute window is for (below).

## Threat: low-confidence settlement

**Vector.** Model answers `yes` on thin evidence; contract settles money anyway.

**Mitigation.** `confidence == low` rewrites any non-void outcome to `void` and keeps
the original reason in `resolver_note` (287-289). The rewrite runs **inside `_fetch`,
before the comparative check**, so it is part of what validators agree on rather than a
post-consensus surprise; the comparative rule additionally requires exact equality on
`confidence` (321-324), so no round can settle on two payloads that would store
different confidences. Covered by the direct-VM test `test_low_confidence_voids` and
preflight checks 05, 06, 18.

**Residual.** The model can *overstate* its confidence. There is no calibration beyond
the model's own label.

## Threat: claim before finality (appeal vs payout)

**Vector.** A bettor claims against a provisional `RESOLVED` outcome, then a dispute
overturns it — the winning side under the *new* outcome claims too, and both sides
draw from the same pool.

**Mitigation.** Three gates in `claim_winnings` (lines 215-220) make this impossible:

1. `status == DISPUTED` → revert `Market under dispute` — an open appeal blocks every
   claim, on both sides.
2. `status == RESOLVED` → required; `DISPUTED` never satisfies it.
3. `now >= resolved_at + 86400` → revert `Dispute window not closed` — claims open only
   after the window closes.

The boundaries are **disjoint**: `raise_dispute` requires `now < resolved_at + 86400`
(line 364) while claims require `now >= resolved_at + 86400` (line 219). At the exact
boundary second a claim is admissible and a dispute is not, regardless of transaction
order, so no claim can be followed by an appeal. An **upheld** ruling re-stamps
`resolved_at` (line 399), reopening a full window before anything can be claimed; a
**rejected** ruling leaves `resolved_at` alone, and the original window still applies.
Covered by `test_claim_blocked_until_dispute_window_closes`,
`test_appeal_cannot_make_both_sides_claimable`, and preflight check 21.

**Residual.** If the owner never rules on a dispute the market stays `DISPUTED` and
claims stay blocked — value is locked, not stolen. The owner is the only liveness
backstop (same trust assumption as `force_resolve`).

**Why `refund_void` is not gated.** It requires `status == VOIDED`, which is terminal:
`void_market` only runs pre-outcome (OPEN/LOCKED), and `raise_dispute` only accepts
`RESOLVED`. A `VOIDED` market can never be appealed, so its refunds cannot conflict
with anything.

## Threat: prompt injection through the market's source page

**Vector.** `resolution_url` content is appended to the prompt after the instructions
(281). A hostile page can say "respond yes". Anyone may create a market, so a
`resolution_url` can be chosen adversarially.

**Mitigation.** The injected text can only influence the *outcome string*; it can never
reach a transfer amount. Everything is observable: prompt, `resolver_note`,
`resolver_sources`, `resolved_at` are stored and returned by `get_market`. A wrong
outcome is appealable for 24h by any bettor (line 364), and the owner can
`re_resolve`/`force_resolve`. Preflight check 14 caps the body at 6000 chars (line 313),
so the page cannot blow up prompt size.

**Residual.** If every validator fetches the same poisoned page they will agree —
consensus proves *agreement with the page*, not *truth of the page*. The economic back
stop is the dispute window plus the fact that a creator with a rigged page still has to
get bettors to stake real GEN on it. This contract does **not** verify that
`resolution_url` is a neutral source; that is a client-side curation problem.

## Threat: page changes or a validator's fetch fails mid-round

**Vector.** Leader sees `yes`, validators see a page that just flipped to `no`, or a
fetch times out.

**Mitigation.** The comparative rule requires exact equality on `outcome` *and*
`confidence` (321-324); disagreement aborts the round with **no state change**. The
market stays `LOCKED` and can be re-attempted (`re_resolve`) or explicitly overridden
(`force_resolve`). Failing-to-agree is strictly safer than agreeing on a stale snapshot.

**Residual.** A market whose source is permanently flapping may need `force_resolve`.

## Threat: double spend via re-entrancy

**Vector.** `claim_winnings` and `refund_void` move value; a recipient that can run code
during a transfer might re-enter and claim twice.

**Mitigation.** Both functions are ordered checks-effects-interactions for **every**
leg: in `claim_winnings`, `b.claimed = True` and the storage write happen at lines
234-235, *before* the fee transfer (236-237) and the bettor transfer (238-239); in
`refund_void` at 252-253, before 254. The re-entrant call then sees `claimed == True`
and reverts `Already claimed` (preflight check 09 enforces that no `emit_transfer` in
either function precedes the `claimed` write — the owner fee leg included).

**Residual.** None found in the ordering itself. Do not move any transfer above the
`claimed` write, and do not assume `emit_transfer` is call-free without checking the
platform semantics.

## Threat: owner override abuse

**Vector.** Owner force-settles markets against the evidence, or raises the fee after
bets are placed.

**Mitigation.** Overrides are attributable: `force_resolve` prefixes `[FORCED]` (355),
`resolve_dispute` prefixes `[DISPUTE UPHELD]`/`[DISPUTE REJECTED]` (398, 403), and
`get_market` exposes the note. `set_fee` is capped at 500 bps (567), and the fee is
**copied into each market at creation** (line 135) — later `set_fee` calls cannot change
the fee of a market that already exists.

**Residual.** Owner can still pick the outcome outright. Trusting the owner is a
deployment decision, not a code property.

## Threat: ownership theft

**Vector.** `transfer_ownership` called by an impostor, or the owner key leaks.

**Mitigation.** Every owner method checks `sender == self.owner` first (344, 384, 412,
568, 577) and reverts `Only owner`. Signing happens in the local encrypted keystore
(`~/.genlayer/keystores`), never in the CLI arguments.

**Residual.** A leaked owner key grants `force_resolve`/`set_fee`/`transfer_ownership`.
No multi-sig, timelock, or two-step transfer exists — that is a non-goal below.

## Threat: dispute manipulation

**Vector.** Non-bettors spam disputes; a bettor disputes repeatedly; disputes arrive
after the window.

**Mitigation.** `raise_dispute` requires `status == RESOLVED` (362), `now < resolved_at + 86400`
(364), and a bet by the sender on that market (366-374). Creating the dispute sets
`status = DISPUTED` (378), so a second dispute fails the status check, and every claim
fails on the same flag until the owner rules. Only the owner rules (384) and only once
(389). The window is strictly bounded while the claim gate uses the inclusive
complement (`>=` at line 219), so dispute and claim eligibility can never hold at the
same instant.

**Residual — window reset asymmetry.** When a dispute is **upheld**, `resolved_at` is
re-stamped (399), re-opening a fresh 24h window on the new outcome; when it is
**rejected**, `resolved_at` is left alone, so the original window keeps running — but
claims stay blocked for the whole time the market is `DISPUTED`. That is safe in both
directions (an upheld change deserves its own appeal window), but a client tracking
"window closed" must re-read `resolved_at` after every ruling rather than caching it.

## Threat: griefing through permissionless transitions

**Vector.** Anyone can call `lock_market` / `request_resolution` / `void_market`.

**Mitigation.** All three are time- or role-gated: lock needs `now >= close_time`
(141-149) and `OPEN`; resolution needs `now >= resolve_time` and `LOCKED` (258-263);
`void_market` needs creator-or-owner and pre-outcome status (152-161). Once resolved,
`request_resolution` cannot be replayed (status check), so LLM rounds are bounded to one
per lock plus owner-driven `re_resolve`.

**Residual.** Anyone can pay for the first resolution round; the creator can cancel a
market while `OPEN`/`LOCKED` (bettors are made whole via refunds, but lose the
opportunity).

## Threat: state growth / view denial of service

**Vector.** Millions of markets make list views expensive; unbounded pages let a client
pull the whole state in one call.

**Mitigation.** `limit` is clamped to 50 (452), `offset` floored at 0 (450), bets are
resolved through `market_bets`/`bettor_bets` indexes instead of scans, and body size /
note length are bounded (313, 280).

**Residual.** `_market_ids` still materializes and sorts *all* market ids per call
(448-449) — O(n) in market count. Fine at current scale (1 market); a fork expecting
heavy growth should add a cursor or a reverse index.

## Threat: funds that can never leave

**Vector.** Value enters the contract without a corresponding claim path.

**Mitigation / status.**
- `fund_market` splits value across both pools with no bet attached — corrupts the
  parimutuel ratio and is unclaimable (README §5). **Known flaw, kept only because it
  is in the deployed bytecode; do not call it.** First deletion in any production fork.
- Integer division leaves rounding dust in the contract; there is no sweep function.
  Dust is stranded by design, and no one can be robbed by it.

**Residual.** Both are value-*locking*, not value-*stealing*. Documented rather than
hidden.

## Non-goals

- No resolution bond/slashing, so a bad resolution costs its author nothing.
- No multi-outcome markets, no AMM, no partial fills.
- No multi-sig or timelock on ownership.
- No guarantee that `resolution_url` points at a neutral source.
- No frontend/wallet security — this repository is contract + tests only.
