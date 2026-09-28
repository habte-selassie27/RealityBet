#!/usr/bin/env bash
set -euo pipefail

RB_CONTRACT="${RB_CONTRACT:-0xe4e87d989ce6Cc4FeaD89273596B6398a26cB177}"
MID="${MID:-}"
BID="${BID:-}"
BETTOR="${BETTOR:-}"
DID="${DID:-}"
PAGE_OFFSET="${PAGE_OFFSET:-0}"
PAGE_SIZE="${PAGE_SIZE:-10}"
CLOSE_WAIT="${CLOSE_WAIT:-120}"
RETRIES="${RETRIES:-60}"
INTERVAL="${INTERVAL:-3000}"
SMOKE_ACCOUNT="${SMOKE_ACCOUNT:-rabby}"
TRANSCRIPT="${TRANSCRIPT:-}"
MODE="${1:-read}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
umask 077

READS_PASSED=0
READS_FAILED=0
READS_SKIPPED=0
LAST_TX_HASH=""

usage() {
  printf '%s\n' \
    "Usage:" \
    "  scripts/smoke.sh                 read-only: every view method plus one expected revert" \
    "  scripts/smoke.sh --write         create/lock/resolve a market, then all reads" \
    "  scripts/smoke.sh --help          show this help" \
    "" \
    "Environment:" \
    "  RB_CONTRACT   deployed contract address" \
    "  MID/BID/DID   market, bet, dispute id; discovered from the chain when omitted" \
    "  BETTOR        bettor address (with or without 0x) for the address-index views" \
    "  PAGE_OFFSET   first market id to page over (default: 0)" \
    "  PAGE_SIZE     page length (default: 10)" \
    "  TRANSCRIPT    append the sanitized session log to this file" \
    "  RETRIES       receipt polling attempts in write mode (default: 60)" \
    "  INTERVAL      receipt polling interval in ms (default: 3000)" \
    "  SMOKE_ACCOUNT active unlocked account for write mode (default: rabby)" \
    "" \
    "Write mode creates a real market, waits CLOSE_WAIT seconds, locks it and runs" \
    "consensus resolution, spending network fees. Bets cannot be placed from the CLI" \
    "(place_bet is payable and the CLI always sends value 0) — place them through the" \
    "Studio UI or genlayer-js, then re-run with MID/BID set."
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v genlayer >/dev/null 2>&1 || fail "genlayer is required"
}

require_int() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || fail "$name must be a non-negative integer, got: $value"
}

# Read failure dumps can embed validator node_config (private keys, API key env
# names). Nothing raw leaves this function: every line is filtered before it is
# printed or appended to the transcript.
sanitize() {
  grep -Ev "private_key|api_key|mnemonic|seed_phrase|keystore_password" || true
}

record() {
  local line="$1"
  printf '%s\n' "$line"
  if [[ -n "$TRANSCRIPT" ]]; then
    printf '%s\n' "$line" >>"$TRANSCRIPT"
  fi
}

run_read() {
  local label="$1"
  local method="$2"
  shift 2
  local output status
  set +e
  output="$(genlayer call "$RB_CONTRACT" "$method" "$@" 2>&1)"
  status=$?
  set -e
  if [[ -n "$TRANSCRIPT" ]]; then
    {
      printf '$ genlayer call %s %s%s\n' "$RB_CONTRACT" "$method" "${*:+ $*}"
      printf '%s\n' "$output"
      printf '\n'
    } | sanitize >>"$TRANSCRIPT"
  fi
  if [[ $status -eq 0 ]]; then
    printf 'read: %s — ok\n' "$label"
    printf '%s\n' "$output" | sanitize | awk '/^Result:/{f=1;next} f&&!/successfully executed/{print "  "$0}'
    READS_PASSED=$((READS_PASSED + 1))
    return 0
  fi
  printf 'read: %s — FAILED (exit %d)\n' "$label" "$status" >&2
  printf '%s\n' "$output" | sanitize | grep -E "Error during read|✖|GenLayer RPC error" | sed 's/^/  /' >&2 || true
  READS_FAILED=$((READS_FAILED + 1))
  return 0
}

skip_read() {
  printf 'read: %s — skipped (%s)\n' "$1" "$2"
  READS_SKIPPED=$((READS_SKIPPED + 1))
}

# A reverting read is part of the smoke: the contract must answer an unknown id
# with the exact gl.vm.UserError string, not a crash.
expect_revert() {
  local method="$1"
  local id="$2"
  local expected="$3"
  local output status decoded
  set +e
  output="$(genlayer call "$RB_CONTRACT" "$method" --args "$id" 2>&1)"
  status=$?
  set -e
  if [[ $status -eq 0 ]]; then
    printf 'revert: %s(%s) — FAILED, call succeeded\n' "$method" "$id" >&2
    READS_FAILED=$((READS_FAILED + 1))
    return 0
  fi
  # The receipt carries both execution_result: 'ERROR' and the base64-encoded
  # UserError string in result: '...'; decode every candidate and keep the
  # longest printable one.
  decoded=""
  while IFS= read -r candidate; do
    [[ -z "$candidate" ]] && continue
    local attempt
    attempt="$(printf '%s' "$candidate" | base64 -d 2>/dev/null | tr -cd '[:print:]' || true)"
    if [[ -n "$attempt" && ${#attempt} -gt ${#decoded} ]]; then
      decoded="$attempt"
    fi
  done < <(printf '%s\n' "$output" \
    | grep -oE "result: '[A-Za-z0-9+/=]+'" \
    | sed -E "s/result: '(.*)'/\1/")
  if [[ -n "$TRANSCRIPT" ]]; then
    {
      printf '$ genlayer call %s %s --args %s\n' "$RB_CONTRACT" "$method" "$id"
      printf '  exited %d; decoded gl.vm.UserError: %q\n' "$status" "$decoded"
      printf '\n'
    } >>"$TRANSCRIPT"
  fi
  if [[ "$decoded" == "$expected" ]]; then
    printf 'revert: %s(%s) — ok (%s)\n' "$method" "$id" "$expected"
    READS_PASSED=$((READS_PASSED + 1))
  else
    printf 'revert: %s(%s) — FAILED, got %q\n' "$method" "$id" "$decoded" >&2
    READS_FAILED=$((READS_FAILED + 1))
  fi
}

extract_tx_hash() {
  awk '
    /Hash:/ || /hash:/ {
      if (match($0, /0x[0-9a-fA-F]{64}/)) {
        print substr($0, RSTART, RLENGTH)
        exit
      }
      if (getline > 0 && match($0, /0x[0-9a-fA-F]{64}/)) {
        print substr($0, RSTART, RLENGTH)
        exit
      }
    }
  ' "$1"
}

wait_finalized() {
  local tx_hash="$1"
  local receipt_file="$2"

  if ! genlayer receipt "$tx_hash" --status FINALIZED --retries "$RETRIES" --interval "$INTERVAL" >"$receipt_file" 2>&1; then
    printf 'finalization failed for %s\n' "$tx_hash" >&2
    return 1
  fi
  grep -q "status_name: 'FINALIZED'" "$receipt_file" || {
    printf 'transaction did not finalize: %s\n' "$tx_hash" >&2
    return 1
  }
  grep -q "execution_result: 'SUCCESS'" "$receipt_file" || {
    printf 'transaction execution was not successful: %s\n' "$tx_hash" >&2
    return 1
  }
}

write_and_wait() {
  local method="$1"
  shift
  local write_file="$TMP_DIR/write.log"
  local receipt_file="$TMP_DIR/receipt.log"
  local tx_hash

  record "write: $method $*"
  if ! genlayer write "$RB_CONTRACT" "$method" "$@" >"$write_file" 2>&1; then
    printf 'write failed: %s\n' "$method" >&2
    sanitize <"$write_file" | head -20 >&2
    return 1
  fi
  tx_hash="$(extract_tx_hash "$write_file")"
  [[ "$tx_hash" =~ ^0x[0-9a-fA-F]{64}$ ]] || {
    printf 'could not determine transaction hash for %s\n' "$method" >&2
    return 1
  }
  record "  transaction: $tx_hash"
  wait_finalized "$tx_hash" "$receipt_file"
  LAST_TX_HASH="$tx_hash"
}

discover_mid() {
  local output
  if ! output="$(genlayer call "$RB_CONTRACT" get_market_ids --args "$PAGE_OFFSET" "$PAGE_SIZE" 2>&1)"; then
    return 1
  fi
  printf '%s' "$output" | grep -oE 'm[0-9]+-[0-9]+' | head -1
}

discover_bid() {
  local output
  if ! output="$(genlayer call "$RB_CONTRACT" get_market_bets --args "$1" 2>&1)"; then
    return 1
  fi
  printf '%s' "$output" | grep -oE 'b[0-9]+-[0-9]+' | head -1
}

discover_did() {
  local output
  if ! output="$(genlayer call "$RB_CONTRACT" get_market --args "$1" 2>&1)"; then
    return 1
  fi
  printf '%s' "$output" | grep -oE 'd[0-9]+-[0-9]+' | head -1
}

run_reads() {
  if [[ -n "$TRANSCRIPT" ]]; then
    : >"$TRANSCRIPT"
  fi

  record "# RealityBet read smoke — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  record "# contract: $RB_CONTRACT"
  record ""

  run_read get_platform_stats get_platform_stats
  run_read "get_market_ids(page)" get_market_ids --args "$PAGE_OFFSET" "$PAGE_SIZE"
  run_read "get_markets_page(page)" get_markets_page --args "$PAGE_OFFSET" "$PAGE_SIZE"

  if [[ -z "$MID" ]]; then
    MID="$(discover_mid || true)"
  fi

  if [[ -n "$MID" ]]; then
    run_read "get_market($MID)" get_market --args "$MID"
    run_read "get_market_stats($MID)" get_market_stats --args "$MID"
    run_read "get_odds($MID)" get_odds --args "$MID"
    run_read "get_market_bets($MID)" get_market_bets --args "$MID"
    run_read "get_market_bets_detailed($MID)" get_market_bets_detailed --args "$MID"
    if [[ -z "$BID" ]]; then
      BID="$(discover_bid "$MID" || true)"
    fi
    if [[ -z "$DID" ]]; then
      DID="$(discover_did "$MID" || true)"
    fi
  else
    skip_read get_market "no market on chain (total_markets is 0)"
    skip_read get_market_stats "no market on chain"
    skip_read get_odds "no market on chain"
    skip_read get_market_bets "no market on chain"
    skip_read get_market_bets_detailed "no market on chain"
  fi

  if [[ -n "$BID" ]]; then
    run_read "get_bet($BID)" get_bet --args "$BID"
  else
    skip_read get_bet "no bet id (CLI cannot place payable bets)"
  fi

  if [[ -n "$BETTOR" ]]; then
    run_read "get_bets_by_bettor" get_bets_by_bettor --args "$BETTOR"
    run_read "get_bettor_bets" get_bettor_bets --args "$BETTOR"
  else
    skip_read get_bets_by_bettor "set BETTOR=0x<address>"
    skip_read get_bettor_bets "set BETTOR=0x<address>"
  fi

  if [[ -n "$DID" ]]; then
    run_read "get_dispute($DID)" get_dispute --args "$DID"
  else
    skip_read get_dispute "no dispute id on chain"
  fi

  expect_revert get_market "m-does-not-exist" "Market not found"

  record ""
  record "reads passed: $READS_PASSED, failed: $READS_FAILED, skipped: $READS_SKIPPED"
  [[ "$READS_FAILED" -eq 0 ]] || return 1
}

run_write_lifecycle() {
  local now close resolve
  now="$(date +%s)"
  close=$((now + CLOSE_WAIT))
  resolve=$((now + CLOSE_WAIT + 60))

  write_and_wait create_market --args \
    "Smoke: will this market reach consensus?" \
    "Read-only smoke market created by scripts/smoke.sh; resolves YES." \
    "https://example.com/smoke" \
    '["tech"]' \
    "$close" "$resolve"
  MID="$(discover_mid || true)"
  [[ -n "$MID" ]] || fail "could not discover the market id created by create_market"
  record "  market: $MID (close in ${CLOSE_WAIT}s)"

  printf 'waiting %ds for close_time...\n' "$CLOSE_WAIT"
  sleep "$CLOSE_WAIT"

  write_and_wait lock_market --args "$MID"
  write_and_wait request_resolution --args "$MID"

  run_read "get_market($MID)" get_market --args "$MID"
  run_read "get_market_stats($MID)" get_market_stats --args "$MID"
}

case "$MODE" in
  --help|-h)
    usage
    exit 0
    ;;
  read|--read)
    require_command
    [[ "$RB_CONTRACT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail "RB_CONTRACT is not a valid address"
    printf 'target: %s (read-only)\n' "$RB_CONTRACT"
    run_reads
    ;;
  --write)
    require_command
    [[ "$RB_CONTRACT" =~ ^0x[0-9a-fA-F]{40}$ ]] || fail "RB_CONTRACT is not a valid address"
    require_int RETRIES "$RETRIES"
    require_int INTERVAL "$INTERVAL"
    require_int CLOSE_WAIT "$CLOSE_WAIT"
    network_output="$(genlayer config get network 2>&1)" || fail "could not read the GenLayer network"
    network="$(awk -F= '/^network=/{print $2; exit}' <<< "$network_output")"
    [[ "$network" == "studionet" ]] || fail "write mode requires network=studionet (found: ${network:-unknown})"
    account_output="$(genlayer account show 2>&1)" || fail "could not read the active GenLayer account"
    [[ "$account_output" == *"name: '$SMOKE_ACCOUNT'"* ]] || fail "active account must be $SMOKE_ACCOUNT"
    [[ "$account_output" == *"status: 'unlocked'"* ]] || fail "$SMOKE_ACCOUNT must be unlocked"
    [[ "$account_output" == *"active: true"* ]] || fail "$SMOKE_ACCOUNT must be the active account"
    printf 'target: %s (writes enabled)\n' "$RB_CONTRACT"
    printf 'wallet: %s is active and unlocked; writes will spend network fees\n' "$SMOKE_ACCOUNT"
    run_write_lifecycle
    run_reads
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
