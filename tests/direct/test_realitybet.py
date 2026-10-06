import json
from datetime import datetime, timezone


def _ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _make_market(contract, close_ts, resolve_ts, categories=None):
    return contract.create_market(
        "Will BTC close above $100k on Dec 31 2025?",
        "Resolves YES if BTC closes above 100k.",
        "https://coinmarketcap.com/currencies/bitcoin/",
        categories if categories is not None else ["crypto"],
        close_ts,
        resolve_ts,
    )


def _setup_open_market(direct_vm, contract):
    direct_vm.warp("2025-01-01T00:00:00Z")
    now = _ts("2025-01-01T00:00:00Z")
    return _make_market(contract, now + 1000, now + 2000)


def _mock_resolution(direct_vm, outcome, confidence="high"):
    direct_vm.mock_web(".*", {"status": 200, "body": "<html>BTC price $105000</html>"})
    direct_vm.mock_llm(".*", json.dumps({
        "outcome": outcome,
        "confidence": confidence,
        "reason": "Event confirmed by primary source.",
        "sources_checked": ["https://coinmarketcap.com/currencies/bitcoin/"],
    }))


def _warp_past_dispute_window(direct_vm):
    """Resolution runs at 2025-01-01T01:00:00Z; claims open exactly 24h later."""
    direct_vm.warp("2025-01-02T01:00:00Z")


def test_create_market(direct_vm, direct_deploy, direct_owner):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    m = contract.get_market(mid)
    assert m["title"].startswith("Will BTC")
    assert m["status"] == "open"
    assert m["outcome"] == ""
    assert m["pool_yes"] == 0 and m["pool_no"] == 0
    assert m["resolver_confidence"] == "" and m["resolver_sources"] == ""
    assert m["categories"] == ["crypto"]
    assert m["category"] == "crypto"


def test_create_market_multi_categories(direct_vm, direct_deploy, direct_owner):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    direct_vm.warp("2025-01-01T00:00:00Z")
    now = _ts("2025-01-01T00:00:00Z")
    mid = _make_market(contract, now + 1000, now + 2000, ["crypto", "finance", "economy"])
    m = contract.get_market(mid)
    assert m["categories"] == ["crypto", "finance", "economy"]
    assert m["category"] == "crypto"


def test_create_market_dedupes_and_caps_categories(direct_vm, direct_deploy, direct_owner):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    direct_vm.warp("2025-01-01T00:00:00Z")
    now = _ts("2025-01-01T00:00:00Z")
    mid = _make_market(
        contract, now + 1000, now + 2000,
        ["Crypto", "crypto", "sports", "tech", "science", "world", "custom"],
    )
    m = contract.get_market(mid)
    assert m["categories"] == ["crypto", "sports", "tech", "science", "world"]


def test_create_market_invalid_category_reverts(direct_vm, direct_deploy, direct_owner):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    direct_vm.warp("2025-01-01T00:00:00Z")
    now = _ts("2025-01-01T00:00:00Z")
    with direct_vm.expect_revert("Invalid category"):
        _make_market(contract, now + 1000, now + 2000, ["crypto", "not-a-category"])
    with direct_vm.expect_revert("At least one category"):
        _make_market(contract, now + 1000, now + 2000, [])


def test_place_bet_yes_no(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 3000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0
    assert bid_yes != bid_no
    odds = contract.get_odds(mid)
    assert odds["pool_yes"] == 1000
    assert odds["pool_no"] == 3000
    assert odds["total_pool"] == 4000
    assert odds["yes"] == 25 and odds["no"] == 75


def test_lock_market(direct_vm, direct_deploy, direct_owner):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    with direct_vm.expect_revert("not closed"):
        contract.lock_market(mid)
    direct_vm.warp("2025-01-01T00:20:00Z")
    assert contract.lock_market(mid) is True
    assert contract.get_market(mid)["status"] == "locked"


def test_resolve_yes_pays_winner(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 6000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 4000
    contract.place_bet(mid, "no")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    assert contract.request_resolution(mid) is True
    m = contract.get_market(mid)
    assert m["outcome"] == "yes"
    assert m["resolver_confidence"] == "high"
    assert "coinmarketcap.com" in m["resolver_sources"]
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_alice
    payout = int(contract.claim_winnings(bid_yes))
    # gross=(6000*10000)//6000=10000, fee=10000*150//10000=150, net=9850
    assert payout == 9850


def test_resolve_no_pays_winner(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 2000
    contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 8000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "no")
    contract.request_resolution(mid)
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_bob
    payout = int(contract.claim_winnings(bid_no))
    # gross=(8000*10000)//8000=10000, fee=150, net=9850
    assert payout == 9850


def test_resolve_void_refunds(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    bid = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "void")
    contract.request_resolution(mid)
    assert contract.get_market(mid)["outcome"] == "void"
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_alice
    assert int(contract.claim_winnings(bid)) == 5000


def test_low_confidence_voids(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes", confidence="low")
    contract.request_resolution(mid)
    m = contract.get_market(mid)
    assert m["outcome"] == "void"
    assert "Low confidence" in m["resolver_note"]
    assert m["resolver_confidence"] == "low"
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_alice
    assert int(contract.claim_winnings(bid)) == 1000


def test_duplicate_claim_reverts(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    contract.request_resolution(mid)
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_alice
    contract.claim_winnings(bid)
    with direct_vm.expect_revert("Already claimed"):
        contract.claim_winnings(bid)


def test_dispute_flow(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    contract.request_resolution(mid)
    direct_vm.sender = direct_alice
    did = contract.raise_dispute(mid, "Primary source misread")
    assert contract.get_market(mid)["status"] == "disputed"
    direct_vm.sender = direct_owner
    assert contract.resolve_dispute(did, True, "no", "Manual review: event did not occur") is True
    m = contract.get_market(mid)
    assert m["status"] == "resolved" and m["outcome"] == "no"
    d = contract.get_dispute(did)
    assert d["resolved"] is True and d["outcome"] == "upheld"


def test_loser_gets_zero(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    contract.request_resolution(mid)
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_bob
    assert int(contract.claim_winnings(bid_no)) == 0


def test_fee_split(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    assert contract.set_fee(200) is True
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 3000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    contract.place_bet(mid, "no")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    contract.request_resolution(mid)
    assert contract.get_market(mid)["fee_bps"] == 200
    _warp_past_dispute_window(direct_vm)
    direct_vm.sender = direct_alice
    payout = int(contract.claim_winnings(bid_yes))
    # gross=(3000*4000)//3000=4000, fee=4000*200//10000=80, net=3920
    assert payout == 3920


def test_market_ids_page_newest_first(direct_vm, direct_deploy, direct_owner):
    """11 markets so the newest id (m10) would sort before m2 as a plain string."""
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    direct_vm.warp("2025-01-01T00:00:00Z")
    now = _ts("2025-01-01T00:00:00Z")
    created = [_make_market(contract, now + 1000, now + 2000) for _ in range(11)]

    assert len(created) == 11
    assert contract.get_market_ids(0, 11) == list(reversed(created))
    assert contract.get_market_ids(0, 3) == created[-1:-4:-1]
    assert contract.get_market_ids(10, 5) == [created[0]]
    assert contract.get_market_ids(99, 5) == []
    assert contract.get_market_ids(0, 999) == list(reversed(created))  # capped at 50

    page = contract.get_markets_page(0, 2)
    assert [m["id"] for m in page] == created[-1:-3:-1]
    assert page[0]["title"].startswith("Will BTC")
    assert page[0]["status"] == "open"


def test_market_bets_detailed_newest_first(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 3000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0

    assert contract.get_market_bets(mid) == [bid_yes, bid_no]
    detailed = contract.get_market_bets_detailed(mid)
    assert [b["id"] for b in detailed] == [bid_no, bid_yes]
    assert detailed[1]["side"] == "yes" and detailed[1]["amount"] == 1000
    assert detailed[0]["side"] == "no" and detailed[0]["amount"] == 3000
    assert detailed[0]["claimed"] is False
    assert contract.get_market_bets_detailed("m-does-not-exist") == []


def test_bets_by_bettor_matches_raw_and_hex(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    """Regression: get_bettor_bets stripped 0x from the query but not from the
    stored key, so it never matched and always returned []. Callers pass the
    address as a hex string, so both the 0x-prefixed and bare forms must hit."""
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 3000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0

    # The address as stored on chain (0x-prefixed hex) and the same value
    # without its prefix must both resolve.
    alice_hex = "0x" + direct_alice.hex()
    bare = direct_alice.hex()
    alice_bets = contract.get_bets_by_bettor(alice_hex)
    assert len(alice_bets) == 1
    assert alice_bets[0]["id"] == bid_yes
    assert alice_bets[0]["side"] == "yes"
    assert alice_bets[0]["market_id"] == mid
    assert alice_bets[0]["bettor"].lower() == alice_hex

    assert contract.get_bettor_bets(alice_hex) == [bid_yes]
    assert contract.get_bettor_bets(bare) == [bid_yes]
    assert [b["id"] for b in contract.get_bets_by_bettor(bare)] == [bid_yes]

    assert [b["id"] for b in contract.get_bets_by_bettor("0x" + direct_bob.hex())] == [bid_no]
    assert contract.get_bets_by_bettor("0xdeadbeef") == []


def test_claim_blocked_until_dispute_window_closes(direct_vm, direct_deploy, direct_owner, direct_alice):
    """Claims open exactly at resolved_at + 86400; the dispute window closes on the
    same instant (strictly), so no appeal can be filed after a payout."""
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    with direct_vm.expect_revert("Market not resolved"):
        contract.claim_winnings(bid)
    contract.request_resolution(mid)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Dispute window not closed"):
        contract.claim_winnings(bid)
    direct_vm.warp("2025-01-02T00:59:59Z")
    with direct_vm.expect_revert("Dispute window not closed"):
        contract.claim_winnings(bid)

    direct_vm.warp("2025-01-02T01:00:00Z")
    with direct_vm.expect_revert("Dispute window is 24h"):
        contract.raise_dispute(mid, "too late")
    assert int(contract.claim_winnings(bid)) == 985


def test_appeal_cannot_make_both_sides_claimable(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    """An appeal flips the outcome only while no side has been paid: claims are
    blocked while disputed, and the ruling restarts the 24h window."""
    direct_vm.sender = direct_owner
    contract = direct_deploy("contracts/RealityBet.py")
    mid = _setup_open_market(direct_vm, contract)
    direct_vm.sender = direct_alice
    direct_vm.value = 1000
    bid_yes = contract.place_bet(mid, "yes")
    direct_vm.value = 0
    direct_vm.sender = direct_bob
    direct_vm.value = 1000
    bid_no = contract.place_bet(mid, "no")
    direct_vm.value = 0
    direct_vm.warp("2025-01-01T00:20:00Z")
    contract.lock_market(mid)
    direct_vm.warp("2025-01-01T01:00:00Z")
    _mock_resolution(direct_vm, "yes")
    contract.request_resolution(mid)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Dispute window not closed"):
        contract.claim_winnings(bid_yes)
    did = contract.raise_dispute(mid, "source misread")
    with direct_vm.expect_revert("Market under dispute"):
        contract.claim_winnings(bid_yes)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("Market under dispute"):
        contract.claim_winnings(bid_no)

    direct_vm.sender = direct_owner
    contract.resolve_dispute(did, True, "no", "manual review")
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Dispute window not closed"):
        contract.claim_winnings(bid_yes)

    _warp_past_dispute_window(direct_vm)
    assert int(contract.claim_winnings(bid_yes)) == 0
    direct_vm.sender = direct_bob
    # gross=(1000*2000)//1000=2000, fee=2000*150//10000=30, net=1970
    assert int(contract.claim_winnings(bid_no)) == 1970
