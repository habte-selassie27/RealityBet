# Calling RealityBet from the CLI / SDK

Deployed contract (Studio, studionet):

```
0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177
```

The deployed code is byte-identical to [`contracts/RealityBet.py`](contracts/RealityBet.py)
— verify with `genlayer code <address> | diff - contracts/RealityBet.py`.

## Preflight

```bash
genlayer network set studionet
genlayer config get          # activeAccount + rpc_url
genlayer account list        # funded accounts

C=0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177
```

## Argument syntax

`--args` is variadic: **every** value after it is an argument, so quoted strings with
spaces must be quoted, and a method with no args takes no `--args` at all.

| Contract type | CLI form |
|---|---|
| `string` | `--args "m0-1770000000"` (quote anything with spaces) |
| `int` / `u256` | `--args 3600` (large values auto-widen to BigInt) |
| `bool` | `--args true` |
| `address` | `--args 0x04e0353B7218b66D6803725ce7342E6e1225DB1b` |
| `list[str]` | `--args '["crypto","finance"]'` |

`--fee-value` is the transaction *fee deposit*, not the amount sent to a payable
method — omit it and let Studio derive the fee.

---

## Reads — `genlayer call` (12 views)

```bash
genlayer call $C get_platform_stats
genlayer call $C get_market_ids        --args 0 10      # paginated, newest first, cap 50
genlayer call $C get_markets_page      --args 0 10      # same ids, full dicts
genlayer call $C get_market            --args "$MID"    # full market record
genlayer call $C get_market_stats      --args "$MID"    # status, pools, bet counts
genlayer call $C get_odds              --args "$MID"    # implied yes/no percentages
genlayer call $C get_market_bets       --args "$MID"    # [bet_id] oldest first
genlayer call $C get_market_bets_detailed --args "$MID" # full bet dicts, newest first
genlayer call $C get_bet               --args "$BID"
genlayer call $C get_bets_by_bettor    --args "0x$ALICE"   # full bet dicts
genlayer call $C get_bettor_bets       --args "0x$ALICE"   # [bet_id] only
genlayer call $C get_dispute           --args "$DID"
```

`get_bets_by_bettor` / `get_bettor_bets` accept the address with or without the `0x`
prefix (both sides of the lookup are normalized).

Verified on-chain: `get_platform_stats` returns
`{ fee_bps: 150, owner: '0x04e0...DB1b', total_markets: 0, total_volume: 0 }`.

Reverts surface as `✖ Error during read operation`, with the `gl.vm.UserError` string
encoded in the `data` field (e.g. reading an unknown id gives `Market not found`).

---

## Writes — `genlayer write` (14 methods)

Owner is `0x04e0353B7218b66D6803725ce7342E6e1225DB1b`. Access control:

| Method | Who may call |
|---|---|
| `create_market` | anyone |
| `place_bet` * | anyone, `OPEN` only, before `close_time` |
| `fund_market` * | anyone, `OPEN` only |
| `lock_market` | anyone, after `close_time` |
| `request_resolution` | anyone, after `resolve_time`, `LOCKED` only |
| `claim_winnings` | the bettor, once per bet, `RESOLVED` only, no open dispute, and only after `resolved_at + 24h` |
| `refund_void` | the bettor, once per bet, `VOIDED` only |
| `raise_dispute` | a bettor on that market, strictly within 24h of resolution (`now < resolved_at + 86400`) |
| `void_market` | market creator or owner, `OPEN`/`LOCKED` only |
| `force_resolve` | **owner** |
| `resolve_dispute` | **owner** |
| `re_resolve` | **owner** |
| `set_fee` | **owner**, ≤ 500 bps |
| `transfer_ownership` | **owner** |

\* payable — see the caveat below.

```bash
# --- market setup -------------------------------------------------------------
CLOSE=$(( $(date +%s) + 3600 ))
RESOLVE=$(( $(date +%s) + 7200 ))

MID=$(genlayer write $C create_market --args \
  "Will BTC close above \$100k on Dec 31 2026?" \
  "Resolves YES if BTC closes above 100k on the last day of 2026." \
  "https://coinmarketcap.com/currencies/bitcoin/" \
  '["crypto"]' \
  $CLOSE $RESOLVE | grep -oE 'm[0-9]+-[0-9]+' | head -1)

# --- lifecycle ----------------------------------------------------------------
genlayer write $C lock_market         --args "$MID"          # after $CLOSE
genlayer write $C request_resolution  --args "$MID"          # after $RESOLVE (runs the LLM)

# --- reading the result -------------------------------------------------------
genlayer call  $C get_market          --args "$MID"
genlayer call  $C get_market_stats    --args "$MID"

# --- disputes (bettor, then owner) -------------------------------------------
DID=$(genlayer write $C raise_dispute --args "$MID" "source contradicted itself" \
  | grep -oE 'd[0-9]+-[0-9]+' | head -1)
genlayer write $C resolve_dispute     --args "$DID" true yes "primary source confirmed" "appeal upheld"
genlayer write $C re_resolve          --args "$MID"          # owner: re-run the LLM
genlayer write $C force_resolve       --args "$MID" void "source offline"   # owner: last resort
genlayer write $C void_market         --args "$MID"          # creator or owner, pre-resolution

# --- payouts (per bet) --------------------------------------------------------
# claim_winnings waits for resolved_at + 24h (reverts "Dispute window not closed")
genlayer write $C claim_winnings      --args "$BID"
genlayer write $C refund_void         --args "$BID"

# --- admin (owner) ------------------------------------------------------------
genlayer write $C set_fee             --args 300             # bps, hard cap 500
genlayer write $C transfer_ownership  --args "0x<new owner>"
```

Every write is a consensus transaction: the CLI waits for the receipt, and a
non-unanimous result comes back with the contract's `gl.vm.UserError` message.

### ⚠️ The CLI cannot call the two payable methods

`genlayer write` hardcodes `value: 0n` (the CLI's `WriteAction`), so `place_bet` and
`fund_market` always see `gl.message.value == 0` and revert with `Must send GEN`.
Neither method has a `--value` flag. Use the Studio UI, or genlayer-js directly:

```js
// npm i genlayer-js viem   —  GEN is 18 decimals, 1 GEN = 10n ** 18n
import { createClient, chains } from "genlayer-js";
import { privateKeyToAccount } from "viem/accounts";

const client = createClient({
  chain: chains.studionet,
  endpoint: "https://studio.genlayer.com/api",
  account: privateKeyToAccount(process.env.GL_KEY),
});

const hash = await client.writeContract({
  address: "0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177",
  functionName: "place_bet",
  args: [process.env.MARKET_ID, "yes"],
  value: 1_000_000_000_000_000_000n, // 1 GEN
});
console.log(await client.waitForTransactionReceipt({ hash }));
```

---

## Inspecting the contract

```bash
genlayer code    $C                                   # deployed source
genlayer schema  $C                                   # every method, param type, payable flag
genlayer call    $C get_platform_stats                 # smoke test
```

## Signing from the terminal

The CLI never talks to a browser wallet. `genlayer write` signs with the private key
stored in an **encrypted keystore** at `~/.genlayer/keystores/<name>.json`, and only
`genlayer account` commands accept `--account`:

```bash
genlayer account list                    # name, address, which is active
genlayer account show --account rabby     # address + balance + locked/unlocked
genlayer account use rabby               # ← this is what `genlayer write` signs with
genlayer account import --name rabby --private-key 0x<64 hex>   # from your Rabby account
genlayer account unlock --account rabby  # cache the key in the OS keychain → no password prompt
genlayer account lock   --account rabby  # drop it from the keychain again
genlayer account send <to> <amount> --account main   # fund the account you'll write from
```

If the account is locked, `genlayer write` prompts for the keystore password inline
(3 attempts, then it gives up). `unlock` caches the key so subsequent writes are silent.

Writes and funding therefore come from the keystore address, **not** the Rabby
extension — Rabby only holds the same key if you exported it from Rabby to the CLI.
Owner-gated methods (`set_fee`, `force_resolve`, `resolve_dispute`, `re_resolve`,
`transfer_ownership`) need the keystore whose address is the deployed contract's owner.
The current deploy is owned by `0x04e0353B7218b66D6803725ce7342E6e1225DB1b`; if that
isn't one of your CLI keystores, those methods revert with `Only owner` until you
import that key or `transfer_ownership` to an address you control.


## Smoke test

```bash
scripts/smoke.sh            # read-only: every view + one expected revert
scripts/smoke.sh --write    # creates a market, locks it, runs consensus (spends fees)
```

`smoke.sh` never places a bet (`place_bet` is payable and the CLI cannot send value).
A captured run lives at [`examples/smoke-read-transcript.txt`](examples/smoke-read-transcript.txt).

## Local

```bash
pytest tests -q                        # 19 direct-VM tests, no network needed
python3 scripts/preflight.py           # 24 offline AST + behavior checks
genlayer up
genlayer deploy contracts/RealityBet.py
genlayer network set localnet
```
