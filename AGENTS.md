# RealityBet — repo guide for coding agents

GenLayer Intelligent Contract that runs YES/NO prediction markets settled by validator
consensus over live web data. Contract + tests only: there is no frontend, no wallet
layer, and no off-chain service in this repo.

## Layout

```
contracts/RealityBet.py        # the entire contract (580 lines)
tests/direct/test_realitybet.py# 19 direct-VM tests (mock_web / mock_llm)
COMMANDS.md                    # genlayer call/write reference for all 26 methods
README.md                      # consensus design, state design, money, method reference
docs/                          # ARCHITECTURE, CONSENSUS, INTEGRATION, THREAT_MODEL
examples/                      # live CLI transcript + harness-generated payload samples
proof/                         # sanitized deployment / state-read / schema captures
scripts/preflight.py           # offline AST + behavior checks (no network)
scripts/smoke.sh               # read-only live smoke; --write is opt-in and spends fees
```

`genlayer_skills.md` is vendored reference material from the official
`genlayer-dev@genlayerlabs` plugin — do not edit it, and do not treat it as project state.

## Commands

```bash
pytest tests -q        # run the direct-VM suite
python3 scripts/preflight.py             # 24 offline checks, no network
python3 scripts/preflight.py --dump-views examples   # regenerate examples payloads
scripts/smoke.sh                          # read-only live smoke against studionet
genlayer up           # localnet, needed for integration-style checks
genlayer deploy contracts/RealityBet.py   # localnet deploy
```

There is no lint task configured. Match the surrounding style: no type-annotation
shorthands, explicit `u256(...)` casts around arithmetic, `gl.vm.UserError` for every
user-facing failure (never `assert`), snake_case storage fields on the dataclasses.

## Invariants — do not break these

1. **Money is integer math, never LLM output.** The resolver returns an outcome string;
   every amount is computed with `u256` division.
2. **Every transfer runs after `claimed` is set**, fee leg included, so a re-entrant
   call sees `claimed == True` (`claim_winnings` lines 234-239, `refund_void` 252-254).
   Preflight check 09 rejects any `emit_transfer` that precedes the `claimed` write.
3. **Ambiguity resolves to `VOID`, never to a guess.** Unparsable LLM output, low
   confidence, or an unknown outcome string all rewrite to `void` via
   `_settlement_policy` (lines 267-293). This is deliberate — refunds cost less than a
   confidently wrong settlement.
4. **Consensus must bind every field that changes settlement.** The comparative rule
   pins `outcome` *and* `confidence` exactly (lines 321-324), and the policy runs inside
   `_fetch` **before** the comparison (line 319), so validators agree on the final
   post-policy result. Do not relax the rule to `outcome` only — `confidence` rewrites
   `yes`/`no` to `void` after all.
5. **Claims and disputes have disjoint time boundaries.** `claim_winnings` requires
   `now >= resolved_at + 86400` (line 219) and a non-`DISPUTED` status (215-218);
   `raise_dispute` requires `now < resolved_at + 86400` (line 364). Keep one side
   inclusive and the other exclusive — if they can both hold at the same instant, an
   appeal can race a payout against the same pool. An upheld ruling must keep
   re-stamping `resolved_at` (line 399).
6. **`lock_market`, `request_resolution` and `place_bet` guards are time-based and
   permissionless by design.** Do not add caller checks to them; that would let a
   market stay open or stay unsettled past its deadline.
7. **Only the outcome is non-deterministic.** Keep all `gl.nondet.*` calls inside
   `_resolve`'s `_fetch`, otherwise unrelated methods become non-deterministic and stop
   reaching consensus.
8. **Known flaw:** `fund_market` splits funds across both pools with no bet attached, so
   it corrupts the parimutuel ratio and is unclaimable. It is in the deployed contract at
   `0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177`, so it cannot be deleted without
   redeploying and re-pointing the evidence links.

## Editing the contract

The deployed address linked in the README and in the portal submission must match the
source in this repo. Either leave `contracts/RealityBet.py` byte-identical, or redeploy
to Studio and update the address in the README before resubmitting.

## Tests

Direct-VM tests mock both non-deterministic sources:

```python
direct_vm.mock_web(".*", {"status": 200, "body": "<html>...</html>"})
direct_vm.mock_llm(".*", json.dumps({"outcome": "yes", "confidence": "high", ...}))
```

`direct_vm.warp("2025-01-01T00:00:00Z")` sets the VM clock the contract reads in `_now()`.
Address fixtures (`direct_alice`, `direct_bob`) are raw `bytes`; views typed `str` must be
queried with `"0x" + addr.hex()` or `addr.hex()`.
