#!/usr/bin/env python3
"""Offline RealityBet preflight.

This does not replace GenVM Direct Mode. It makes the repository auditable even
on a machine without the GenLayer runtime by checking Python syntax, consensus
boundaries, money-flow invariants, and the deterministic state machine through
a minimal import stub with mocked non-determinism.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from datetime import datetime as _RealDateTime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "RealityBet.py"


class _UserError(Exception):
    pass


class _Generic:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls


class _Map(dict, _Generic):
    def get_or_insert_default(self, key):
        if key not in self:
            self[key] = _Array()
        return self[key]


class _Array(list, _Generic):
    pass


class _Int(int):
    pass


def _normalize(addr: str) -> str:
    s = str(addr).lower().strip()
    if not s.startswith("0x"):
        s = "0x" + s
    return s


class _Address(str):
    def __new__(cls, value="0x" + "00" * 20):
        return super().__new__(cls, _normalize(value))

    def __format__(self, spec):
        if spec in ("", "x"):
            return str(self)
        return str.__format__(self, spec)

    def __eq__(self, other):
        try:
            return _normalize(self) == _normalize(other)
        except Exception:
            return False

    def __hash__(self):
        return hash(_normalize(self))


class _Decorator:
    def __call__(self, fn):
        return fn

    @property
    def payable(self):
        return self


class _Public:
    view = _Decorator()
    write = _Decorator()


class _Recorder:
    def __init__(self, addr, log):
        self.addr = addr
        self.log = log

    def emit_transfer(self, value):
        self.log.append((_normalize(self.addr), int(value)))


class _Clock:
    ts = 0

    @classmethod
    def now(cls, tz=None):
        return _RealDateTime.fromtimestamp(cls.ts, tz=tz)


def _install_stub() -> None:
    module = types.ModuleType("genlayer")
    gl = types.SimpleNamespace()
    gl.Contract = object
    gl.public = _Public()
    gl.vm = types.SimpleNamespace(UserError=_UserError)
    gl.message = types.SimpleNamespace(
        sender_address=_Address("0x" + "ee" * 20), value=_Int(0)
    )
    gl.transfers = []
    gl.get_contract_at = lambda addr: _Recorder(addr, gl.transfers)
    gl.nondet = types.SimpleNamespace()
    gl.eq_principle = types.SimpleNamespace()
    module.gl = gl
    module.allow_storage = lambda cls: cls
    module.TreeMap = _Map
    module.DynArray = _Array
    module.u8 = _Int
    module.u32 = _Int
    module.u256 = _Int
    module.Address = _Address
    sys.modules["genlayer"] = module


def _load_contract():
    _install_stub()
    spec = importlib.util.spec_from_file_location("realitybet_contract", CONTRACT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.datetime = _Clock
    return module


def _chain(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return None
    return ".".join(reversed(parts))


def _parents(tree) -> dict:
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _calls(tree) -> list[tuple[str, ast.Call, str]]:
    parents = _parents(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if chain is None:
            continue
        scope = node
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        found.append((chain, node, scope.name if scope else "<module>"))
    return found


def _ast_checks(source: str) -> list[str]:
    tree = ast.parse(source)
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    rb = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RealityBet")
    fn_names = {node.name for node in ast.walk(rb) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    checks = []
    required_classes = {"Market", "Bet", "Dispute", "MarketStatus", "Outcome", "Category", "RealityBet"}
    assert required_classes <= class_names, required_classes - class_names
    checks.append("required storage/state classes present")

    views, writes, payable = [], [], []
    for fn in rb.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            name = _chain(dec)
            if name == "gl.public.view":
                views.append(fn.name)
            elif name == "gl.public.write":
                writes.append(fn.name)
            elif name == "gl.public.write.payable":
                writes.append(fn.name)
                payable.append(fn.name)
    assert len(views) == 12, f"expected 12 views, found {len(views)}"
    assert len(writes) == 14, f"expected 14 writes, found {len(writes)}"
    assert sorted(payable) == ["fund_market", "place_bet"], payable
    checks.append("public surface is 12 views + 14 writes (payable: fund_market, place_bet)")

    nondet = [(c, f) for c, _n, f in _calls(tree) if "nondet" in c]
    assert len(nondet) == 2, f"expected 2 gl.nondet calls, found {len(nondet)}: {nondet}"
    assert all(f in ("_fetch", "_resolve") for c, f in nondet), nondet
    assert sorted(c for c, _f in nondet) == ["gl.nondet.exec_prompt", "gl.nondet.web.get"]
    comparative = [(c, f) for c, _n, f in _calls(tree) if c.endswith("prompt_comparative")]
    assert len(comparative) == 1 and comparative[0][1] == "_resolve", comparative
    checks.append("all non-determinism confined to _fetch inside _resolve (2 nondet + 1 comparative)")

    asserts = [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert not asserts, f"found {len(asserts)} assert statements; user errors must raise gl.vm.UserError"
    user_errors = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
    assert len(user_errors) >= 40, f"expected >=40 raised user errors, found {len(user_errors)}"
    checks.append("no assert statements; failures raise gl.vm.UserError")

    transfers = []
    parents = _parents(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "emit_transfer":
            continue
        scope_node = node
        while scope_node is not None and not isinstance(
            scope_node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            scope_node = parents.get(scope_node)
        scope = scope_node.name if scope_node else "<module>"
        value = next((kw.value for kw in node.keywords if kw.arg == "value"), None)
        assert value is not None, f"{scope}: emit_transfer without value= keyword"
        label = _chain(value) or type(value).__name__
        transfers.append((scope, label, node.lineno))
    assert len(transfers) == 3, f"expected 3 emit_transfer calls, found {len(transfers)}"
    allowed = {"fee", "payout", "b"}
    for scope, label, _line in transfers:
        base = label.split(".")[0]
        assert base in allowed, f"{scope}: transfer value {label!r} is not payout/fee/stake"
    checks.append("every emit_transfer value is fee, payout, or the original stake (never LLM output)")

    for fn in (node for node in ast.walk(rb) if isinstance(node, ast.FunctionDef)):
        if fn.name not in ("claim_winnings", "refund_void"):
            continue
        claimed = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Attribute) and t.attr == "claimed"]
        assert claimed, f"{fn.name}: no claimed assignment found"
        for chain, call, _scope in _calls(fn):
            if not chain.endswith("emit_transfer"):
                continue
            value = next((kw.value for kw in call.keywords if kw.arg == "value"), None)
            label = _chain(value) if value is not None else ""
            if label in ("fee",):
                continue
            assert call.lineno > claimed[0], (
                f"{fn.name}: stake/payout transfer at line {call.lineno} runs before "
                f"claimed is set at line {claimed[0]}"
            )
    checks.append("bettor-facing transfers (payout, refund) happen after claimed is set")

    assert "u256(150)" in source and "u256(500)" in source
    checks.append("fee defaults to 150 bps and is hard-capped at 500 bps")
    assert "limit <= 0 or limit > 50" in source
    checks.append("pagination is capped at 50 per page")
    assert "86400" in source
    checks.append("dispute window is 24h (86400s)")
    assert "[:6000]" in source
    checks.append("web page body fed to the LLM is bounded to 6000 chars")
    assert "sort_keys=True" in source
    checks.append("LLM JSON is key-sorted before the comparative check for stable diffs")
    return checks


def _expect(fn, message: str) -> None:
    try:
        fn()
    except _UserError as err:
        assert message in str(err), f"expected {message!r}, got {err!r}"
        return
    raise AssertionError(f"expected revert {message!r}, but call succeeded")


def _fresh(mod, sender: str = "0x" + "aa" * 20):
    gl = mod.gl
    gl.message.sender_address = _Address(sender)
    gl.message.value = _Int(0)
    gl.transfers.clear()
    gl.nondet.web = types.SimpleNamespace(get=lambda url: types.SimpleNamespace(body=b"<html>btc 105000</html>"))
    gl.nondet.exec_prompt = lambda prompt: _llm_payload
    gl.eq_principle.prompt_comparative = lambda fn, rule: _comparative_payload(fn)
    contract = mod.RealityBet()
    contract.markets = _Map()
    contract.bets = _Map()
    contract.disputes = _Map()
    contract.market_bets = _Map()
    contract.bettor_bets = _Map()
    return contract


_llm_payload = '{"outcome": "yes", "confidence": "high", "reason": "confirmed", "sources_checked": ["https://src"]}'
_comparative_payload = lambda fn: fn()


def _set_llm(payload) -> None:
    global _llm_payload
    _llm_payload = payload


def _set_comparative(payload) -> None:
    global _comparative_payload
    _comparative_payload = payload if callable(payload) else (lambda fn: payload)


def _ts(iso: str) -> int:
    return int(_RealDateTime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _warp(mod, iso: str) -> None:
    _Clock.ts = _ts(iso)


def _create(contract, cats=None, offset=(1000, 2000)):
    now = _Clock.ts
    return contract.create_market(
        "Will BTC close above $100k on Dec 31 2026?",
        "Resolves YES if BTC closes above 100k.",
        "https://coinmarketcap.com/currencies/bitcoin/",
        cats if cats is not None else ["crypto"],
        now + offset[0],
        now + offset[1],
    )


def _open_market(mod, contract):
    _warp(mod, "2026-01-01T00:00:00Z")
    return _create(contract)


def _lock_and_reach_resolve(mod, contract, mid):
    _warp(mod, "2026-01-01T00:20:00Z")
    contract.lock_market(mid)
    _warp(mod, "2026-01-01T00:40:00Z")


def _bet(mod, contract, mid, sender, amount, side):
    mod.gl.message.sender_address = _Address(sender)
    mod.gl.message.value = _Int(amount)
    bid = contract.place_bet(mid, side)
    mod.gl.message.value = _Int(0)
    return bid


ALICE = "0x" + "a1" * 20
BOB = "0x" + "b2" * 20
OWNER = "0x" + "ee" * 20


def check_creation(mod) -> str:
    _set_comparative(lambda fn: fn())
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    m = contract.get_market(mid)
    assert m["status"] == "open" and m["outcome"] == ""
    assert m["categories"] == ["crypto"] and m["category"] == "crypto"
    assert m["pool_yes"] == 0 and m["pool_no"] == 0
    _warp(mod, "2026-01-01T00:00:00Z")
    now = _Clock.ts
    expect = [
        (lambda: contract.create_market("t", "d", "u", ["crypto"], now - 1, now + 1), "Close time must be future"),
        (lambda: contract.create_market("t", "d", "u", ["crypto"], now + 2, now + 1), "Resolve after close"),
        (lambda: contract.create_market("t", "d", "u", ["nope"], now + 2, now + 3), "Invalid category"),
        (lambda: contract.create_market("t", "d", "u", [], now + 2, now + 3), "At least one category"),
    ]
    for fn, msg in expect:
        _expect(fn, msg)
    mid2 = _create(contract, ["Crypto", "crypto", "sports", "tech", "science", "world", "custom"])
    assert contract.get_market(mid2)["categories"] == ["crypto", "sports", "tech", "science", "world"]
    return "create_market validates time/category rules and dedupes + caps tags at 5"


def check_betting(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    _expect(lambda: _bet(mod, contract, mid, ALICE, 0, "yes"), "Must send GEN")
    _expect(lambda: _bet(mod, contract, mid, ALICE, 1000, "maybe"), "Side must be yes or no")
    _bet(mod, contract, mid, ALICE, 1000, "yes")
    _bet(mod, contract, mid, BOB, 3000, "no")
    odds = contract.get_odds(mid)
    assert odds == {"yes": 25, "no": 75, "pool_yes": 1000, "pool_no": 3000, "total_pool": 4000}, odds
    assert contract.get_platform_stats()["total_volume"] == 4000
    _warp(mod, "2026-01-01T00:20:00Z")
    _expect(lambda: _bet(mod, contract, mid, ALICE, 500, "yes"), "Betting closed")
    return "place_bet splits pools (25/75 odds), rejects zero value, bad side, and closed betting"


def check_lifecycle_guards(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    _expect(lambda: contract.lock_market(mid), "not closed")
    _warp(mod, "2026-01-01T00:20:00Z")
    assert contract.lock_market(mid) is True
    assert contract.get_market(mid)["status"] == "locked"
    _expect(lambda: contract.lock_market(mid), "Market not open")
    _expect(lambda: _bet(mod, contract, mid, ALICE, 100, "yes"), "Market not open")
    _expect(lambda: contract.request_resolution(mid), "Too early to resolve")
    mid2 = _open_market(mod, contract)
    _warp(mod, "2026-01-01T00:40:00Z")
    _expect(lambda: contract.request_resolution(mid2), "Must be locked first")
    _expect(lambda: contract.void_market(mid), "Not authorized")
    mod.gl.message.sender_address = _Address(ALICE)
    _expect(lambda: contract.void_market(mid), "Not authorized")
    return "lock/resolve/void guards are time-gated and permissionless only where intended"


def check_resolution(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    _bet(mod, contract, mid, ALICE, 1000, "yes")
    _lock_and_reach_resolve(mod, contract, mid)

    _set_llm('{"outcome": "yes", "confidence": "high", "reason": "ok", "sources_checked": ["https://src"]}')
    assert contract.request_resolution(mid) is True
    m = contract.get_market(mid)
    assert m["outcome"] == "yes" and m["status"] == "resolved"
    assert m["resolver_confidence"] == "high" and "src" in m["resolver_sources"]
    assert m["resolved_at"] == _Clock.ts

    mid2 = _open_market(mod, contract)
    _lock_and_reach_resolve(mod, contract, mid2)
    _set_llm('{"outcome": "yes", "confidence": "low", "reason": "unsure", "sources_checked": []}')
    contract.request_resolution(mid2)
    m2 = contract.get_market(mid2)
    assert m2["outcome"] == "void" and m2["status"] == "resolved"
    assert "Low confidence" in m2["resolver_note"]

    mid3 = _open_market(mod, contract)
    _lock_and_reach_resolve(mod, contract, mid3)
    _set_llm('{"outcome": "maybe", "confidence": "high", "reason": "x"}')
    contract.request_resolution(mid3)
    m3 = contract.get_market(mid3)
    assert m3["outcome"] == "void" and "Invalid AI outcome" in m3["resolver_note"]

    mid4 = _open_market(mod, contract)
    _lock_and_reach_resolve(mod, contract, mid4)
    _set_llm('{"result": "no", "conf": "high", "explanation": "alias keys"}')
    contract.request_resolution(mid4)
    assert contract.get_market(mid4)["outcome"] == "no"

    mid5 = _open_market(mod, contract)
    _lock_and_reach_resolve(mod, contract, mid5)
    _set_comparative("this is not json {{{")
    contract.request_resolution(mid5)
    m5 = contract.get_market(mid5)
    assert m5["status"] == "voided" and m5["resolver_note"] == "AI parse error voided"
    _set_comparative(lambda fn: fn())
    return "resolution: happy path, low-confidence auto-void, bad outcome void, key aliases, parse failure void"


def check_payouts(mod) -> str:
    contract = _fresh(mod, OWNER)
    contract.set_fee(150)
    mid = _open_market(mod, contract)
    bid_yes = _bet(mod, contract, mid, ALICE, 6000, "yes")
    bid_no = _bet(mod, contract, mid, BOB, 4000, "no")
    _lock_and_reach_resolve(mod, contract, mid)
    mod.gl.message.sender_address = _Address(OWNER)
    _expect(lambda: contract.force_resolve("m-nope", "yes", "x"), "Market not found")
    mod.gl.message.sender_address = _Address(ALICE)
    _expect(lambda: contract.force_resolve(mid, "yes", "x"), "Only owner")
    mod.gl.message.sender_address = _Address(OWNER)
    _expect(lambda: contract.force_resolve(mid, "maybe", "x"), "Invalid outcome")
    assert contract.force_resolve(mid, "yes", "manual check") is True
    assert contract.get_market(mid)["resolver_note"] == "[FORCED] manual check"

    mod.gl.transfers.clear()
    mod.gl.message.sender_address = _Address(BOB)
    _expect(lambda: contract.claim_winnings(bid_yes), "Not your bet")
    mod.gl.message.sender_address = _Address(ALICE)
    payout = int(contract.claim_winnings(bid_yes))
    assert payout == 9850, payout
    assert mod.gl.transfers == [(_normalize(OWNER), 150), (_normalize(ALICE), 9850)], mod.gl.transfers
    _expect(lambda: contract.claim_winnings(bid_yes), "Already claimed")
    mod.gl.message.sender_address = _Address(BOB)
    assert int(contract.claim_winnings(bid_no)) == 0
    assert len(mod.gl.transfers) == 2

    mid2 = _open_market(mod, contract)
    bid2 = _bet(mod, contract, mid2, ALICE, 5000, "yes")
    mod.gl.message.sender_address = _Address(OWNER)
    assert contract.void_market(mid2) is True
    mod.gl.transfers.clear()
    mod.gl.message.sender_address = _Address(BOB)
    _expect(lambda: contract.refund_void(bid2), "Not your bet")
    mod.gl.message.sender_address = _Address(ALICE)
    assert contract.refund_void(bid2) is True
    assert mod.gl.transfers == [(_normalize(ALICE), 5000)]
    _expect(lambda: contract.refund_void(bid2), "Already claimed")

    mod.gl.message.sender_address = _Address(OWNER)
    _expect(lambda: contract.set_fee(501), "Max 5 percent")
    assert contract.set_fee(500) is True
    mod.gl.message.sender_address = _Address(ALICE)
    _expect(lambda: contract.set_fee(100), "Only owner")
    _expect(lambda: contract.transfer_ownership(_Address(ALICE)), "Only owner")
    return "payout math is exact (9850 net + 150 fee), losers get 0, refunds are full, admin capped"


def check_disputes(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    bid = _bet(mod, contract, mid, ALICE, 1000, "yes")
    _lock_and_reach_resolve(mod, contract, mid)
    _set_llm('{"outcome": "yes", "confidence": "high", "reason": "ok"}')
    contract.request_resolution(mid)

    mod.gl.message.sender_address = _Address(BOB)
    _expect(lambda: contract.raise_dispute(mid, "no bet"), "Only bettors can dispute")
    mod.gl.message.sender_address = _Address(ALICE)
    did = contract.raise_dispute(mid, "source misread")
    assert contract.get_market(mid)["status"] == "disputed"
    _expect(lambda: contract.raise_dispute(mid, "again"), "Can only dispute resolved")
    _warp(mod, "2026-01-01T01:00:00Z")
    mid2 = _open_market(mod, contract)
    _bet(mod, contract, mid2, ALICE, 100, "yes")
    _lock_and_reach_resolve(mod, contract, mid2)
    contract.request_resolution(mid2)
    _warp(mod, "2026-01-02T02:00:00Z")
    _expect(lambda: contract.raise_dispute(mid2, "late"), "Dispute window is 24h")

    _warp(mod, "2026-01-01T00:45:00Z")
    mod.gl.message.sender_address = _Address(BOB)
    _expect(lambda: contract.resolve_dispute(did, True, "no", "x"), "Only owner")
    mod.gl.message.sender_address = _Address(OWNER)
    assert contract.resolve_dispute(did, True, "no", "manual review") is True
    m = contract.get_market(mid)
    assert m["status"] == "resolved" and m["outcome"] == "no"
    d = contract.get_dispute(did)
    assert d["resolved"] is True and d["outcome"] == "upheld"
    _expect(lambda: contract.resolve_dispute(did, True, "yes", "x"), "Already resolved")
    _expect(lambda: contract.get_dispute("d-nope"), "Dispute not found")
    return "dispute flow: bettor-only, 24h window, owner ruling flips outcome, double-resolve blocked"


def check_re_resolve(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    _bet(mod, contract, mid, ALICE, 1000, "yes")
    _lock_and_reach_resolve(mod, contract, mid)
    _set_llm('{"outcome": "yes", "confidence": "high", "reason": "ok"}')
    contract.request_resolution(mid)
    mod.gl.message.sender_address = _Address(ALICE)
    did = contract.raise_dispute(mid, "doubt")
    mod.gl.message.sender_address = _Address(OWNER)
    _expect(lambda: contract.re_resolve("m-nope"), "Market not found")
    assert contract.re_resolve(mid) is True
    assert contract.get_market(mid)["status"] == "resolved"
    assert contract.get_market(mid)["outcome"] == "yes"
    return "re_resolve re-runs consensus from disputed and settles back to resolved"


def check_views(mod) -> str:
    contract = _fresh(mod, OWNER)
    _warp(mod, "2026-01-01T00:00:00Z")
    created = [_create(contract) for _ in range(51)]
    ids = contract.get_market_ids(0, 999)
    assert len(ids) == 50, len(ids)
    assert ids[0] == created[-1] and ids[-1] == created[1], (ids[0], ids[-1])
    assert created[0] not in ids  # 51 markets, page capped at 50 — oldest is dropped
    assert [int(i.split("-")[0][1:]) for i in ids] == list(range(50, 0, -1))
    assert contract.get_market_ids(49, 5) == [created[1], created[0]]
    assert contract.get_market_ids(51, 5) == []
    page = contract.get_markets_page(0, 2)
    assert [m["id"] for m in page] == [created[-1], created[-2]]
    assert page[0]["status"] == "open"

    mid = created[0]
    bid_yes = _bet(mod, contract, mid, ALICE, 1000, "yes")
    bid_no = _bet(mod, contract, mid, BOB, 3000, "no")
    assert contract.get_market_bets(mid) == [bid_yes, bid_no]
    detailed = contract.get_market_bets_detailed(mid)
    assert [b["id"] for b in detailed] == [bid_no, bid_yes]
    assert detailed[0]["amount"] == 3000 and detailed[0]["claimed"] is False
    assert contract.get_market_bets_detailed("m-none") == []
    assert contract.get_bettor_bets(ALICE) == [bid_yes]
    assert contract.get_bettor_bets(ALICE[2:]) == [bid_yes]
    assert [b["id"] for b in contract.get_bets_by_bettor(ALICE)] == [bid_yes]
    assert contract.get_bets_by_bettor("0xdeadbeef") == []
    assert contract.get_bet(bid_yes)["side"] == "yes"
    stats = contract.get_market_stats(mid)
    assert stats["total_bets"] == 2 and stats["yes_bets"] == 1 and stats["no_bets"] == 1
    _expect(lambda: contract.get_market("m-none"), "Market not found")
    _expect(lambda: contract.get_bet("b-none"), "Bet not found")
    platform = contract.get_platform_stats()
    assert platform["total_markets"] == 51 and platform["fee_bps"] == 150
    assert platform["owner"] == _normalize(OWNER)
    return "views: 50-id pagination with numeric (not lexicographic) order, batched reads, address index"


def check_funding(mod) -> str:
    contract = _fresh(mod, OWNER)
    mid = _open_market(mod, contract)
    _expect(lambda: contract.fund_market(mid), "Must send GEN")
    mod.gl.message.value = _Int(101)
    assert contract.fund_market(mid) is True
    mod.gl.message.value = _Int(0)
    odds = contract.get_odds(mid)
    assert odds["pool_yes"] == 50 and odds["pool_no"] == 51, odds
    _warp(mod, "2026-01-01T00:20:00Z")
    contract.lock_market(mid)
    mod.gl.message.value = _Int(10)
    _expect(lambda: contract.fund_market(mid), "Market not open")
    mod.gl.message.value = _Int(0)
    return "fund_market splits value across both pools while OPEN (documented caveat)"


def _behavior_checks(mod) -> list[str]:
    checks = []
    for fn in (
        check_creation,
        check_betting,
        check_lifecycle_guards,
        check_resolution,
        check_payouts,
        check_disputes,
        check_re_resolve,
        check_views,
        check_funding,
    ):
        checks.append(fn(mod))
    return checks


def _jsonable(value):
    return json.loads(json.dumps(value))


def _capture_resolution(mod, contract, mid, llm_response):
    """Run one resolution and return (market view, exact prompt sent to the LLM)."""
    gl = mod.gl
    captured = {}

    def _record_exec(prompt):
        captured["prompt"] = prompt
        return llm_response

    gl.nondet.exec_prompt = _record_exec
    contract.request_resolution(mid)
    gl.nondet.exec_prompt = lambda prompt: llm_response
    return contract.get_market(mid), captured.get("prompt", "")


def _view_scenario(mod) -> tuple[dict, str]:
    contract = _fresh(mod, OWNER)
    _set_comparative(lambda fn: fn())
    _set_llm('{"outcome": "yes", "confidence": "high", '
             '"reason": "BTC closed the year above 100k.", '
             '"sources_checked": ["https://coinmarketcap.com/currencies/bitcoin/"]}')
    _warp(mod, "2026-01-01T00:00:00Z")
    mid1 = _create(contract)
    bid_yes = _bet(mod, contract, mid1, ALICE, 6000, "yes")
    bid_no = _bet(mod, contract, mid1, BOB, 4000, "no")

    _warp(mod, "2026-01-01T00:20:00Z")
    contract.lock_market(mid1)
    _warp(mod, "2026-01-01T00:40:00Z")
    market1, prompt = _capture_resolution(mod, contract, mid1, _llm_payload)

    mod.gl.message.sender_address = _Address(ALICE)
    payout = int(contract.claim_winnings(bid_yes))

    mid2 = _create(contract, ["crypto", "finance"])
    _bet(mod, contract, mid2, ALICE, 1000, "yes")
    _warp(mod, "2026-01-01T01:00:00Z")
    contract.lock_market(mid2)
    _warp(mod, "2026-01-01T01:15:00Z")
    contract.request_resolution(mid2)
    did = contract.raise_dispute(mid2, "primary source contradicted itself")

    views = {
        "source": (
            "Captured from scripts/preflight.py --dump-views: the same "
            "contracts/RealityBet.py executed in-process with mocked web/LLM "
            "non-determinism, so every field is populated. Live payloads from the "
            "deployed chain are in proof/state-reads.json and "
            "examples/smoke-read-transcript.txt."
        ),
        "market_ids": {"get_market_ids(0, 10)": _jsonable(contract.get_market_ids(0, 10))},
        "markets_page": {"get_markets_page(0, 2)": _jsonable(contract.get_markets_page(0, 2))},
        "market": {
            "get_market(mid1)": _jsonable(contract.get_market(mid1)),
            "get_market(mid2)": _jsonable(contract.get_market(mid2)),
        },
        "market_stats": {"get_market_stats(mid1)": _jsonable(contract.get_market_stats(mid1))},
        "odds": {"get_odds(mid1)": _jsonable(contract.get_odds(mid1))},
        "bets": {
            "get_market_bets(mid1)": _jsonable(contract.get_market_bets(mid1)),
            "get_market_bets_detailed(mid1)": _jsonable(contract.get_market_bets_detailed(mid1)),
            "get_bet(bid_yes)": _jsonable(contract.get_bet(bid_yes)),
            "get_bet(bid_no)": _jsonable(contract.get_bet(bid_no)),
        },
        "bettor_index": {
            "get_bets_by_bettor(alice)": _jsonable(contract.get_bets_by_bettor(ALICE)),
            "get_bettor_bets(alice)": _jsonable(contract.get_bettor_bets(ALICE)),
        },
        "dispute": {"get_dispute(did)": _jsonable(contract.get_dispute(did))},
        "platform": {"get_platform_stats()": _jsonable(contract.get_platform_stats())},
        "observed": {"claimed_payout": payout, "transfers": _jsonable(mod.gl.transfers)},
    }
    return views, prompt


_RESOLUTION_CASES = [
    (
        "happy path — high confidence settles the market",
        '{"outcome": "yes", "confidence": "high", "reason": "BTC closed the year above 100k.", '
        '"sources_checked": ["https://coinmarketcap.com/currencies/bitcoin/"]}',
        "pass-through",
    ),
    (
        "low confidence auto-voids even when the model says yes",
        '{"outcome": "yes", "confidence": "low", "reason": "the page was inconclusive"}',
        "pass-through",
    ),
    (
        "unknown outcome string voids instead of guessing",
        '{"outcome": "maybe", "confidence": "high", "reason": "not stated"}',
        "pass-through",
    ),
    (
        "alias keys (result/conf/explanation) are accepted",
        '{"result": "no", "conf": "high", "explanation": "the event did not occur"}',
        "pass-through",
    ),
    (
        "unparseable LLM output voids the market outright",
        "this is not json {{{",
        "comparative returns the raw string instead of JSON",
    ),
]


def _resolution_examples(mod) -> list[dict]:
    cases = []
    for label, response, note in _RESOLUTION_CASES:
        contract = _fresh(mod, OWNER)
        _set_comparative(lambda fn: fn())
        _warp(mod, "2026-01-01T00:00:00Z")
        mid = _create(contract)
        _bet(mod, contract, mid, ALICE, 1000, "yes")
        _warp(mod, "2026-01-01T00:20:00Z")
        contract.lock_market(mid)
        _warp(mod, "2026-01-01T00:40:00Z")
        if note == "pass-through":
            _set_llm(response)
            _set_comparative(lambda fn: fn())
        else:
            _set_comparative(response)
        market, prompt = _capture_resolution(mod, contract, mid, response)
        cases.append(
            {
                "case": label,
                "llm_response": response,
                "resulting_market": _jsonable(market),
            }
        )
    return cases


def _write_examples(mod, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    views, prompt = _view_scenario(mod)
    (outdir / "view-payloads.json").write_text(
        json.dumps(views, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    cases = _resolution_examples(mod)
    md = [
        "# Resolution prompt and LLM response handling",
        "",
        "Everything below is produced by `scripts/preflight.py --dump-views`, which runs",
        "`contracts/RealityBet.py` in-process with the web fetch and the LLM mocked. The",
        "prompt is the exact string the contract builds in `_resolve`, including the",
        "truncated page body.",
        "",
        "## The prompt the contract sends",
        "",
        "```text",
        prompt.rstrip("\n"),
        "```",
        "",
        "`PAGE CONTENT` is `gl.nondet.web.get(url).body.decode()[:6000]` — capped at 6000",
        "characters before it reaches the model.",
        "",
        "## How responses are interpreted",
        "",
        "The contract parses the JSON defensively: `outcome`/`result`/`verdict`,",
        "`confidence`/`conf`, `reason`/`explanation`/`analysis` are all accepted aliases.",
        "Anything that is not `yes`/`no`/`void` is rewritten to `void`; `confidence: low`",
        "rewrites any non-void outcome to `void`. An unparseable response voids the whole",
        "market (`status: voided`).",
        "",
        "| # | Case | LLM response | Resulting outcome/status | Note |",
        "|---|------|--------------|--------------------------|------|",
    ]
    for index, case in enumerate(cases, 1):
        market = case["resulting_market"]
        md.append(
            f"| {index} | {case['case']} | `{case['llm_response']}` | "
            f"`{market['outcome']}` / `{market['status']}` | {market['resolver_note']} |"
        )
    md += [
        "",
        "## Full resulting market records",
        "",
        "```json",
        json.dumps(cases, indent=2, sort_keys=True, ensure_ascii=False),
        "```",
        "",
    ]
    (outdir / "resolution-prompt.md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {outdir / 'view-payloads.json'}")
    print(f"wrote {outdir / 'resolution-prompt.md'}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--dump-views":
        outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "examples"
        _Clock.ts = _ts("2026-01-01T00:00:00Z")
        module = _load_contract()
        _write_examples(module, outdir)
        return 0

    source = CONTRACT.read_text(encoding="utf-8")
    compile(source, str(CONTRACT), "exec")
    checks = ["contract compiles as Python"]
    checks += _ast_checks(source)
    _Clock.ts = _ts("2026-01-01T00:00:00Z")
    module = _load_contract()
    checks += _behavior_checks(module)

    print(f"RealityBet offline preflight: {len(checks)}/{len(checks)} checks passed")
    for index, check in enumerate(checks, 1):
        print(f"  {index:02d}. PASS - {check}")
    print("\nConsensus/runtime behavior is covered separately by tests/direct/test_realitybet.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
