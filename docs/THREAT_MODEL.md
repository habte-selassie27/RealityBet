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
`sources` (lines 296-308) — all strings, written to `resolver_*` fields. The payout path
(`claim_winnings`, lines 217-234) reads only `b.amount`, `m.pool_*`, `m.fee_bps`,
`m.outcome`. `scripts/preflight.py` check 06 asserts every `emit_transfer(value=...)`
in the file is `fee`, `payout`, or the original stake.

**Residual.** None found: there is no path from a model string to a transfer amount.

## Threat: malformed, missing, or adversarial model output

**Vector.** Reply is not JSON, has the wrong shape, invents an outcome, or hedges.

**Mitigation.** Deterministic normalization (lines 291-329) fails toward `void`:
unparseable → market `VOIDED` (318-323); unknown outcome → `void` (312-314);
`confidence: low` → `void` (309-311). `void` refunds every stake in full (line 218).

**Residual.** A *confidently wrong* high-confidence answer is not detectable here —
that is what the dispute window is for (below).

## Threat: low-confidence settlement

**Vector.** Model answers `yes` on thin evidence; contract settles money anyway.

**Mitigation.** `confidence == low` rewrites any non-void outcome to `void` and keeps
the original reason in `resolver_note` (309-311). Covered by the direct-VM test
`test_resolve_void_refunds`-adjacent low-confidence case and preflight check 16.

**Residual.** The model can *overstate* its confidence. There is no calibration beyond
the model's own label.

## Threat: prompt injection through the market's source page

**Vector.** `resolution_url` content is appended to the prompt after the instructions
(281). A hostile page can say "respond yes". Anyone may create a market, so a
`resolution_url` can be chosen adversarially.

**Mitigation.** The injected text can only influence the *outcome string*; it can never
reach a transfer amount. Everything is observable: prompt, `resolver_note`,
`resolver_sources`, `resolved_at` are stored and returned by `get_market`. A wrong
outcome is appealable for 24h by any bettor (line 354), and the owner can
`re_resolve`/`force_resolve`. Preflight check 11 caps the body at 6000 chars (line 280),
so the page cannot blow up prompt size.

**Residual.** If every validator fetches the same poisoned page they will agree —
consensus proves *agreement with the page*, not *truth of the page*. The economic back
stop is the dispute window plus the fact that a creator with a rigged page still has to
get bettors to stake real GEN on it. This contract does **not** verify that
`resolution_url` is a neutral source; that is a client-side curation problem.

## Threat: page changes or a validator's fetch fails mid-round

**Vector.** Leader sees `yes`, validators see a page that just flipped to `no`, or a
fetch times out.

**Mitigation.** The comparative rule requires `outcome` equality (289); disagreement
aborts the round with **no state change**. The market stays `LOCKED` and can be
re-attempted (`re_resolve`) or explicitly overridden (`force_resolve`).
Failing-to-agree is strictly safer than agreeing on a stale snapshot.

**Residual.** A market whose source is permanently flapping may need `force_resolve`.

## Threat: double spend via re-entrancy

**Vector.** `claim_winnings` and `refund_void` move value; a recipient that can run code
during a transfer might re-enter and claim twice.

**Mitigation.** Betstor-facing transfers are ordered checks-effects-interactions: in
`claim_winnings`, `b.claimed = True` and the storage write happen at lines 231-232,
*before* the bettor transfer at 234; in `refund_void` at 247-248, before 249. The
re-entrant call then sees `claimed == True` and reverts `Already claimed`
(preflight check 07).

**Residual — ordering nuance worth knowing.** The **fee** leg is transferred at line
230, *before* `claimed` is set at 231. If `emit_transfer` ever invoked recipient code
and the owner were a contract with a hostile fallback, that fallback could re-enter
`claim_winnings` while `claimed` is still `False` and recurse, with each frame then
paying the bettor on unwind. On this deployment the owner is an EOA
(`0x04e0...DB1b`) and the contract holds no owner-controlled callback, so the condition
does not hold — but **any production fork should move the fee transfer below the
`claimed` write** (one-line change), and should not assume `emit_transfer` is
call-free without checking the platform semantics.

## Threat: owner override abuse

**Vector.** Owner force-settles markets against the evidence, or raises the fee after
bets are placed.

**Mitigation.** Overrides are attributable: `force_resolve` prefixes `[FORCED]`,
`resolve_dispute` prefixes `[DISPUTE UPHELD]`/`[DISPUTE REJECTED]` (345, 388, 393), and
`get_market` exposes the note. `set_fee` is capped at 500 bps (557), and the fee is
**copied into each market at creation** (line 135) — later `set_fee` calls cannot change
the fee of a market that already exists.

**Residual.** Owner can still pick the outcome outright. Trusting the owner is a
deployment decision, not a code property.

## Threat: ownership theft

**Vector.** `transfer_ownership` called by an impostor, or the owner key leaks.

**Mitigation.** Every owner method checks `sender == self.owner` first (334, 374, 402,
557/566 area) and reverts `Only owner`. Signing happens in the local encrypted keystore
(`~/.genlayer/keystores`), never in the CLI arguments.

**Residual.** A leaked owner key grants `force_resolve`/`set_fee`/`transfer_ownership`.
No multi-sig, timelock, or two-step transfer exists — that is a non-goal below.

## Threat: dispute manipulation

**Vector.** Non-bettors spam disputes; a bettor disputes repeatedly; disputes arrive
after the window.

**Mitigation.** `raise_dispute` requires `status == RESOLVED` (352), a bet by the sender
on that market (357-364), and `now <= resolved_at + 86400` (354). Creating the dispute
sets `status = DISPUTED` (368), so a second dispute fails the status check. Only the
owner rules (374) and only once (379).

**Residual — window reset asymmetry.** When a dispute is **upheld**, `resolved_at` is
re-stamped (389), re-opening a fresh 24h window on the new outcome; when it is
**rejected**, `resolved_at` is left alone, so the original window keeps running. That
is safe in both directions (an upheld change deserves its own appeal window), but a
client tracking "window closed" must re-read `resolved_at` after every ruling rather
than caching it.

## Threat: griefing through permissionless transitions

**Vector.** Anyone can call `lock_market` / `request_resolution` / `void_market`.

**Mitigation.** All three are time- or role-gated: lock needs `now >= close_time`
(141-149) and `OPEN`; resolution needs `now >= resolve_time` and `LOCKED` (253-258);
`void_market` needs creator-or-owner and pre-outcome status (152-161). Once resolved,
`request_resolution` cannot be replayed (status check), so LLM rounds are bounded to one
per lock plus owner-driven `re_resolve`.

**Residual.** Anyone can pay for the first resolution round; the creator can cancel a
market while `OPEN`/`LOCKED` (bettors are made whole via refunds, but lose the
opportunity).

## Threat: state growth / view denial of service

**Vector.** Millions of markets make list views expensive; unbounded pages let a client
pull the whole state in one call.

**Mitigation.** `limit` is clamped to 50 (442), `offset` floored at 0 (440), bets are
resolved through `market_bets`/`bettor_bets` indexes instead of scans, and body size /
note length are bounded (280, 303).

**Residual.** `_market_ids` still materializes and sorts *all* market ids per call
(438-439) — O(n) in market count. Fine at current scale (1 market); a fork expecting
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
