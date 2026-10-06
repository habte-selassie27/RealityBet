# Examples

Real, reproducible artifacts — no hand-written transcripts.

| File | What it is | How to reproduce |
|---|---|---|
| `smoke-read-transcript.txt` | Full CLI session against the deployed contract on studionet: every view call, its result, and one expected revert. | `TRANSCRIPT=examples/smoke-read-transcript.txt scripts/smoke.sh` |
| `view-payloads.json` | Returned payload of all 12 views from one lifecycle (2 markets, 3 bets, 1 dispute, 1 claim). | `python3 scripts/preflight.py --dump-views examples` |
| `resolution-prompt.md` | The exact prompt `_resolve` sends to the model, plus five LLM responses and the market record each one produces. | `python3 scripts/preflight.py --dump-views examples` |

Terminal and IDE screenshots are in [`../proof/screenshots/`](../proof/screenshots/) —
the `create_market` write reaching `MAJORITY_AGREE`, and the read views returning real
stored state.

## Why two sources

`smoke-read-transcript.txt` is a **live** capture: `genlayer call` against
`0x0905C1CE5680AF08354ADF09254AE86fDF66E72D`. It covered 11 of the 12 views; `get_bet`
and `get_dispute` are reported as `skipped` because no bet or dispute exists on chain
(the CLI cannot place a bet — `place_bet` is payable). Pass `MID`/`BID`/`DID`/`BETTOR`
to exercise them once bets exist.

`view-payloads.json` and `resolution-prompt.md` come from the **same contract source**
executed in-process by `scripts/preflight.py`, with `gl.nondet.web.get` and
`gl.nondet.exec_prompt` mocked. That is the only way to show populated payloads and
model outputs without spending fees or depending on live web content.

## Reading the numbers

- 6000 yes + 4000 no staked → winner takes the whole 10000-pool share:
  `10000 * 6000 / 6000 = 10000`, minus the 150 bps fee → `payout 9850`, `fee 150`.
  Both transfers are visible under `observed.transfers` in `view-payloads.json`.
- A voided market pays back full stakes with no fee: `status: voided` through
  `refund_void`, `outcome: void` through `claim_winnings`'s void branch — the latter
  still waits out the 24h dispute window first. Nothing is minted or burned.
- `resolver_sources` is stored as a JSON string (`"[\\"...\\" ]"`), not a list —
  that is the contract's on-chain shape, not a serialization bug in the dump.
