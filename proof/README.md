# Sanitized live proof

These JSON files are sanitized extracts of GenLayer CLI reads against the deployed
contract. They intentionally omit validator private keys, RPC credentials, and
machine-local configuration — in particular, the CLI's read-error output embeds a
node's `private_key` inside `node_config`, so raw error dumps are never stored here;
only the decoded `gl.vm.UserError` string is kept.

| File | Contents |
|---|---|
| `official-deployment.json` | Address, network, deployer, source hashes, and the byte-difference between the deployed source and `contracts/RealityBet.py`. |
| `state-reads.json` | Timestamped `get_platform_stats` / `get_market_ids` / `get_markets_page` outputs plus one expected revert, with the exact command for each. |
| `schema-summary.json` | All 26 methods parsed from `genlayer schema`: parameters, return types, and the two payable methods. |
| [`screenshots/`](screenshots/) | Studio IDE and CLI captures of the same deployment — the `create_market` write reaching `MAJORITY_AGREE`, plus the read views. |

## Deployment

`0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177` on StudioNet, owner
`0x04e0353B7218b66D6803725ce7342E6e1225DB1b` (the deployer — `__init__` assigns
`self.owner = gl.message.sender_address`).

Deployed source is **byte-identical** to this repository's `contracts/RealityBet.py`
except for one extra trailing newline:

- repository: 22056 bytes, sha256 `3d96bfb2dfc27704cce07fcfc8de702c5e88ec385641a9c3f167f1aeb7f9c0a3`
- deployed: 22057 bytes, sha256 `e6286abddf0ea45b1155689593279d74e3065301306f99640e45135a549359f9`

Verify yourself:

```bash
genlayer code 0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177 | diff - contracts/RealityBet.py
```

The deploy transaction hash was never recorded in this repository (Studio UI deploy),
so `deployment_tx` is `null` rather than an invented value.

## Reproducing

```bash
C=0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177
date -u +%Y-%m-%dT%H:%M:%SZ      # captured_at
genlayer call $C get_platform_stats
genlayer call $C get_market_ids        --args 0 10
genlayer call $C get_markets_page      --args 0 10
genlayer schema  $C
scripts/smoke.sh                       # full read session, see examples/
```

Chain state moves (markets get created and resolved), so a fresh run will show
different counters than the ones recorded here; the `captured_at` timestamps mark
what these files describe.
