# Screenshots — deployed contract on Studio (studionet)

Contract `0x0905C1CE5680AF08354ADF09254AE86fDF66E72D`, captured 2026-09-28. The JSON
files in the parent directory are the machine-readable equivalents of these captures;
these images exist so a reviewer can see the raw CLI/IDE output without trusting a
summary.

| File | Shows |
|---|---|
| `studio-run-and-debug.png` | `contracts/RealityBet.py` open in the Studio IDE, "Deployed at: 0xe4…B177", Read/Write Methods auto-derived from the `@gl.public` decorators. |
| `write-create-market.png` | `genlayer write $C create_market` — real arguments in the calldata, `execution_result: 'SUCCESS'`. |
| `write-create-market-receipt.png` | Tail of the same receipt: `MAJORITY_AGREE`, 5 validators committed and revealed, `status_name: 'ACCEPTED'`, `execution_mode: 'NORMAL'`. |
| `read-get-platform-stats.png` | `fee_bps: 150`, `owner: 0x04e0…DB1b`, `total_markets: 1`, `total_volume: 0`. |
| `read-get-market.png` | `get_market m0-1790603379` — the full stored record including the empty resolver fields. |
| `read-get-market-stats.png` | `get_market_stats` — `status: 'open'`, pools and bet counts at 0. |
| `read-get-odds.png` | `get_odds` — `{ yes: 50, no: 50 }`, the documented no-liquidity default. |

The single check that ties source to deployment:

```bash
genlayer code 0x0905C1CE5680AF08354ADF09254AE86fDF66E72D | diff - contracts/RealityBet.py
```

## What is deliberately not shown here

An earlier capture session produced ~40 additional terminal screenshots of write calls.
They are **excluded**, because every one of them shows
`leader_receipt.execution_result: 'ERROR'` — the calls reverted. The reasons are
mechanical, and each one is itself evidence that the contract's guards work:

- **Empty ids.** `$MID` / `$BID` / `$DID` were unset, so calldata went out as
  `{"args":[0],"method":"lock_market"}` → `Market not found`.
- **Owner-gated methods.** The CLI's active keystore is `0x5B3661C5…`; the contract
  owner is `0x04e0353B…`. `set_fee 300` therefore reverted `Only owner`, and
  `get_platform_stats` in `read-get-platform-stats.png` still reports `fee_bps: 150` —
  the guard held and no fee was changed.
- **Payable methods.** `place_bet` and `fund_market` reverted `Must send GEN` because
  the `genlayer` CLI builds every transaction with `value: 0n` hardcoded and exposes no
  flag to change it. `total_volume: 0` in the same capture confirms no bet was recorded.

Publishing those would read as "these methods are broken" rather than "these methods are
guarded, and here is the CLI limitation that prevented exercising them". The paths they
were meant to demonstrate are covered instead by the 19 direct-VM tests, where
`direct_vm.value` and the sender address are set per test.

Raw CLI error dumps are never committed: the read-error output embeds a node's
`private_key` inside `node_config`. Only the decoded `gl.vm.UserError` string is kept
anywhere in this repository.
