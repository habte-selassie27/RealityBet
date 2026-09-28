# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import typing

class MarketStatus:
    OPEN = "open"
    LOCKED = "locked"
    RESOLVED = "resolved"
    VOIDED = "voided"
    DISPUTED = "disputed"


class Outcome:
    YES = "yes"
    NO = "no"
    VOID = "void"

class Category:
    ALL = ["sports", "politics", "crypto", "tech", "science", "entertainment",
           "finance", "economy", "business", "world", "health", "weather",
           "gaming", "esports", "social", "culture", "education", "environment",
           "space", "custom"]


@allow_storage
@dataclass
class Market:
    id: str
    creator: Address
    title: str
    description: str
    resolution_url: str
    categories: DynArray[str]
    close_time: u256
    resolve_time: u256
    outcome: str
    status: str
    pool_yes: u256
    pool_no: u256
    fee_bps: u256
    resolved_at: u256
    resolver_note: str
    resolver_confidence: str
    resolver_sources: str


@allow_storage
@dataclass
class Bet:
    id: str
    market_id: str
    bettor: Address
    side: str
    amount: u256
    claimed: bool
    placed_at: u256


@allow_storage
@dataclass
class Dispute:
    id: str
    market_id: str
    raised_by: Address
    reason: str
    resolved: bool
    outcome: str


class RealityBet(gl.Contract):
    markets: TreeMap[str, Market]
    bets: TreeMap[str, Bet]
    disputes: TreeMap[str, Dispute]
    market_bets: TreeMap[str, DynArray[str]]
    bettor_bets: TreeMap[str, DynArray[str]]
    owner: Address
    platform_fee_bps: u256
    total_volume: u256
    market_count: u256
    bet_count: u256
    dispute_count: u256

    def __init__(self):
        self.owner = gl.message.sender_address
        self.platform_fee_bps = u256(150)
        self.total_volume = u256(0)
        self.market_count = u256(0)
        self.bet_count = u256(0)
        self.dispute_count = u256(0)

    def _now(self) -> u256:
        return u256(int(datetime.now(timezone.utc).timestamp()))

    def _get_market(self, mid: str) -> Market:
        if mid not in self.markets:
            raise gl.vm.UserError("Market not found")
        return self.markets[mid]

    def _get_bet(self, bid: str) -> Bet:
        if bid not in self.bets:
            raise gl.vm.UserError("Bet not found")
        return self.bets[bid]

    def _addr_key(self, a: Address) -> str:
        return format(a, "x")

    @gl.public.write
    def create_market(
        self, title: str, description: str, resolution_url: str, categories: list[str], close_time: u256, resolve_time: u256
    ) -> str:
        now = self._now()
        if not close_time > now:
            raise gl.vm.UserError("Close time must be future")
        if not resolve_time >= close_time:
            raise gl.vm.UserError("Resolve after close")
        cats: list[str] = []
        for c in categories:
            c2 = str(c).lower().strip()
            if c2 not in Category.ALL:
                raise gl.vm.UserError("Invalid category")
            if c2 not in cats:
                cats.append(c2)
        if len(cats) == 0:
            raise gl.vm.UserError("At least one category")
        if len(cats) > 5:
            cats = cats[:5]
        mid = "m" + str(int(self.market_count)) + "-" + str(int(now))
        self.market_count = u256(int(self.market_count) + 1)
        self.markets[mid] = Market(
            mid, gl.message.sender_address, title, description, resolution_url,
            cats, close_time, resolve_time, "", MarketStatus.OPEN,
            u256(0), u256(0), self.platform_fee_bps, u256(0), "", "", "",
        )
        self.market_bets.get_or_insert_default(mid)
        return mid

    @gl.public.write
    def lock_market(self, market_id: str) -> bool:
        m = self._get_market(market_id)
        if not self._now() >= m.close_time:
            raise gl.vm.UserError("Market not closed yet")
        if not m.status == MarketStatus.OPEN:
            raise gl.vm.UserError("Market not open")
        m.status = MarketStatus.LOCKED
        self.markets[market_id] = m
        return True

    @gl.public.write
    def void_market(self, market_id: str) -> bool:
        m = self._get_market(market_id)
        sender = gl.message.sender_address
        if not (sender == m.creator or sender == self.owner):
            raise gl.vm.UserError("Not authorized")
        if m.status not in [MarketStatus.OPEN, MarketStatus.LOCKED]:
            raise gl.vm.UserError("Cannot void at this stage")
        m.status = MarketStatus.VOIDED
        self.markets[market_id] = m
        return True

    @gl.public.write.payable
    def fund_market(self, market_id: str) -> bool:
        m = self._get_market(market_id)
        if not m.status == MarketStatus.OPEN:
            raise gl.vm.UserError("Market not open")
        v = gl.message.value
        if not v > u256(0):
            raise gl.vm.UserError("Must send GEN")
        half = u256(int(v) // 2)
        m.pool_yes = u256(int(m.pool_yes) + int(half))
        m.pool_no = u256(int(m.pool_no) + int(v) - int(half))
        self.total_volume = u256(int(self.total_volume) + int(v))
        self.markets[market_id] = m
        return True

    @gl.public.write.payable
    def place_bet(self, market_id: str, side: str) -> str:
        m = self._get_market(market_id)
        if not m.status == MarketStatus.OPEN:
            raise gl.vm.UserError("Market not open")
        if not self._now() < m.close_time:
            raise gl.vm.UserError("Betting closed")
        s = side.lower().strip()
        if s not in [Outcome.YES, Outcome.NO]:
            raise gl.vm.UserError('Side must be yes or no')
        amount = gl.message.value
        if not amount > u256(0):
            raise gl.vm.UserError("Must send GEN")
        bettor = gl.message.sender_address
        now = self._now()
        bid = "b" + str(int(self.bet_count)) + "-" + str(int(now))
        self.bet_count = u256(int(self.bet_count) + 1)
        self.bets[bid] = Bet(bid, market_id, bettor, s, amount, False, now)
        self.market_bets.get_or_insert_default(market_id).append(bid)
        key = self._addr_key(bettor)
        self.bettor_bets.get_or_insert_default(key).append(bid)
        if s == Outcome.YES:
            m.pool_yes = u256(int(m.pool_yes) + int(amount))
        else:
            m.pool_no = u256(int(m.pool_no) + int(amount))
        self.total_volume = u256(int(self.total_volume) + int(amount))
        self.markets[market_id] = m
        return bid

    @gl.public.write
    def claim_winnings(self, bet_id: str) -> u256:
        b = self._get_bet(bet_id)
        m = self._get_market(b.market_id)
        if not b.bettor == gl.message.sender_address:
            raise gl.vm.UserError("Not your bet")
        if b.claimed:
            raise gl.vm.UserError("Already claimed")
        if not m.status == MarketStatus.RESOLVED:
            raise gl.vm.UserError("Market not resolved")
        payout = u256(0)
        if m.outcome == Outcome.VOID:
            payout = b.amount
        elif b.side == m.outcome:
            total_pool = u256(int(m.pool_yes) + int(m.pool_no))
            win_pool = m.pool_yes if m.outcome == Outcome.YES else m.pool_no
            if int(win_pool) == 0:
                payout = u256(0)
            else:
                gross = u256(int(b.amount) * int(total_pool) // int(win_pool))
                fee = u256(int(gross) * int(m.fee_bps) // 10000)
                payout = u256(int(gross) - int(fee))
                if int(fee) > 0:
                    gl.get_contract_at(self.owner).emit_transfer(value=fee)
        b.claimed = True
        self.bets[bet_id] = b
        if int(payout) > 0:
            gl.get_contract_at(b.bettor).emit_transfer(value=payout)
        return payout

    @gl.public.write
    def refund_void(self, bet_id: str) -> bool:
        b = self._get_bet(bet_id)
        m = self._get_market(b.market_id)
        if not b.bettor == gl.message.sender_address:
            raise gl.vm.UserError("Not your bet")
        if b.claimed:
            raise gl.vm.UserError("Already claimed")
        if not m.status == MarketStatus.VOIDED:
            raise gl.vm.UserError("Market not voided")
        b.claimed = True
        self.bets[bet_id] = b
        gl.get_contract_at(b.bettor).emit_transfer(value=b.amount)
        return True

    @gl.public.write
    def request_resolution(self, market_id: str) -> bool:
        m = self._get_market(market_id)
        if not self._now() >= m.resolve_time:
            raise gl.vm.UserError("Too early to resolve")
        if not m.status == MarketStatus.LOCKED:
            raise gl.vm.UserError("Must be locked first")
        self._resolve(market_id)
        return True

    def _resolve(self, market_id: str) -> None:
        m = self._get_market(market_id)
        url = m.resolution_url
        cat = ", ".join(m.categories)
        prompt = (
            "You are an impartial prediction market resolver with web access.\n"
            'MARKET QUESTION: "' + m.title + '"\nDESCRIPTION: ' + m.description + "\n"
            "CRITERIA: YES if event clearly occurred. NO if clearly NOT occurred. "
            "VOID only if ambiguous, source unavailable, or unanswerable.\n"
            "PRIMARY SOURCE: " + url + "\nCATEGORY: " + cat + "\n"
            "1.Fetch primary source.2.Search corroborating sources.3.Decide yes|no|void."
            "4.Confidence high|medium|low.5.Reason max 2 sentences.\n"
            'Respond ONLY JSON: {"outcome":"yes","confidence":"high",'
            '"reason":"...","sources_checked":["url1"]}'
        )

        def _fetch() -> str:
            web_data = gl.nondet.web.get(url)
            body = web_data.body.decode("utf-8")[:6000]
            full = prompt + "\nPAGE CONTENT:\n" + body
            res = gl.nondet.exec_prompt(full)
            if isinstance(res, dict):
                return json.dumps(res, sort_keys=True)
            cleaned = res.replace("```json", "").replace("```", "").strip()
            return json.dumps(json.loads(cleaned), sort_keys=True)

        raw = gl.eq_principle.prompt_comparative(
            _fetch, "`outcome` must be exactly the same. All other fields must be similar"
        )
        outcome = "void"
        reason = ""
        conf = "low"
        sources = ""
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(result, dict):
                low = {}
                for k, v in result.items():
                    low[str(k).lower().strip()] = v
                outcome = str(low.get("outcome", low.get("result", low.get("verdict", "void")))).lower().strip()
                conf = str(low.get("confidence", low.get("conf", "low"))).lower().strip()
                reason = str(low.get("reason", low.get("explanation", low.get("analysis", ""))))[:500]
                srcs = low.get("sources_checked", low.get("sources", []))
                if isinstance(srcs, list):
                    sources = json.dumps([str(s) for s in srcs])
                elif isinstance(srcs, str):
                    sources = srcs
                if conf == "low" and outcome != "void":
                    outcome = "void"
                    reason = "Low confidence auto-void. Original: " + reason
                if outcome not in [Outcome.YES, Outcome.NO, Outcome.VOID]:
                    outcome = "void"
                    reason = "Invalid AI outcome voided"
            else:
                outcome = "void"
                reason = "AI parse error voided"
        except Exception:
            m.status = MarketStatus.VOIDED
            m.resolver_note = "AI parse error voided"
            m.resolver_confidence = "low"
            self.markets[market_id] = m
            return
        m.outcome = outcome
        m.status = MarketStatus.RESOLVED
        m.resolved_at = self._now()
        m.resolver_note = reason
        m.resolver_confidence = conf
        m.resolver_sources = sources
        self.markets[market_id] = m

    @gl.public.write
    def force_resolve(self, market_id: str, outcome: str, note: str) -> bool:
        if not gl.message.sender_address == self.owner:
            raise gl.vm.UserError("Only owner")
        m = self._get_market(market_id)
        if m.status not in [MarketStatus.LOCKED, MarketStatus.DISPUTED]:
            raise gl.vm.UserError("Invalid state")
        o = outcome.lower().strip()
        if o not in [Outcome.YES, Outcome.NO, Outcome.VOID]:
            raise gl.vm.UserError("Invalid outcome")
        m.outcome = o
        m.status = MarketStatus.RESOLVED
        m.resolved_at = self._now()
        m.resolver_note = "[FORCED] " + note
        self.markets[market_id] = m
        return True

    @gl.public.write
    def raise_dispute(self, market_id: str, reason: str) -> str:
        m = self._get_market(market_id)
        if not m.status == MarketStatus.RESOLVED:
            raise gl.vm.UserError("Can only dispute resolved")
        if not self._now() <= u256(int(m.resolved_at) + 86400):
            raise gl.vm.UserError("Dispute window is 24h")
        sender = gl.message.sender_address
        found = False
        if market_id in self.market_bets:
            for bid in self.market_bets[market_id]:
                if self.bets[bid].bettor == sender:
                    found = True
                    break
        if not found:
            raise gl.vm.UserError("Only bettors can dispute")
        did = "d" + str(int(self.dispute_count)) + "-" + str(int(self._now()))
        self.dispute_count = u256(int(self.dispute_count) + 1)
        self.disputes[did] = Dispute(did, market_id, sender, reason, False, "")
        m.status = MarketStatus.DISPUTED
        self.markets[market_id] = m
        return did

    @gl.public.write
    def resolve_dispute(self, dispute_id: str, upheld: bool, new_outcome: str, note: str) -> bool:
        if not gl.message.sender_address == self.owner:
            raise gl.vm.UserError("Only owner")
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        if d.resolved:
            raise gl.vm.UserError("Already resolved")
        m = self._get_market(d.market_id)
        if upheld:
            o = new_outcome.lower().strip()
            if o not in [Outcome.YES, Outcome.NO, Outcome.VOID]:
                raise gl.vm.UserError("Invalid outcome")
            m.outcome = o
            m.status = MarketStatus.RESOLVED
            m.resolver_note = "[DISPUTE UPHELD] " + note
            m.resolved_at = self._now()
            d.outcome = "upheld"
        else:
            m.status = MarketStatus.RESOLVED
            m.resolver_note = "[DISPUTE REJECTED] " + note
            d.outcome = "rejected"
        d.resolved = True
        self.markets[d.market_id] = m
        self.disputes[dispute_id] = d
        return True

    @gl.public.write
    def re_resolve(self, market_id: str) -> bool:
        if not gl.message.sender_address == self.owner:
            raise gl.vm.UserError("Only owner")
        m = self._get_market(market_id)
        if not m.status == MarketStatus.DISPUTED:
            raise gl.vm.UserError("Market not disputed")
        m.status = MarketStatus.LOCKED
        self.markets[market_id] = m
        self._resolve(market_id)
        return True

    def _market_dict(self, m: Market) -> dict:
        cats = list(m.categories)
        return {"id": m.id, "creator": format(m.creator, "x"), "title": m.title,
                "description": m.description, "resolution_url": m.resolution_url,
                "category": cats[0] if len(cats) > 0 else "custom", "categories": cats,
                "close_time": int(m.close_time), "resolve_time": int(m.resolve_time),
                "outcome": m.outcome, "status": m.status,
                "pool_yes": int(m.pool_yes), "pool_no": int(m.pool_no),
                "fee_bps": int(m.fee_bps), "resolved_at": int(m.resolved_at),
                "resolver_note": m.resolver_note, "resolver_confidence": m.resolver_confidence,
                "resolver_sources": m.resolver_sources}

    def _bet_dict(self, b: Bet) -> dict:
        return {"id": b.id, "market_id": b.market_id, "bettor": format(b.bettor, "x"),
                "side": b.side, "amount": int(b.amount), "claimed": b.claimed,
                "placed_at": int(b.placed_at)}

    def _seq(self, mid: str) -> int:
        """Sort key for "m<count>-<timestamp>" ids — string order would put m10 before m2."""
        try:
            return int(str(mid).split("-")[0][1:])
        except Exception:
            return 0

    def _market_ids(self, offset: int, limit: int) -> list[str]:
        """Stored market ids, newest first, paginated. limit 0 means "up to the cap" (50)."""
        ids: list[str] = [str(k) for k in self.markets]
        ids.sort(key=self._seq)
        if offset < 0:
            offset = 0
        if limit <= 0 or limit > 50:
            limit = 50
        out: list[str] = []
        i = len(ids) - 1 - offset
        while i >= 0 and len(out) < limit:
            out.append(ids[i])
            i -= 1
        return out

    @gl.public.view
    def get_market(self, market_id: str) -> dict:
        return self._market_dict(self._get_market(market_id))

    @gl.public.view
    def get_market_ids(self, offset: int, limit: int) -> typing.Any:
        return self._market_ids(offset, limit)

    @gl.public.view
    def get_markets_page(self, offset: int, limit: int) -> typing.Any:
        """Full market dicts for a page of ids — one RPC call instead of N get_market calls."""
        out: typing.Any = []
        for mid in self._market_ids(offset, limit):
            out.append(self._market_dict(self._get_market(mid)))
        return out

    @gl.public.view
    def get_bet(self, bet_id: str) -> dict:
        return self._bet_dict(self._get_bet(bet_id))

    @gl.public.view
    def get_market_bets_detailed(self, market_id: str) -> typing.Any:
        """Every bet on a market as full dicts, newest first."""
        out: typing.Any = []
        if market_id not in self.market_bets:
            return out
        bids = list(self.market_bets[market_id])
        i = len(bids) - 1
        while i >= 0:
            out.append(self._bet_dict(self._get_bet(bids[i])))
            i -= 1
        return out

    def _bettor_bet_ids(self, bettor: str) -> list[str]:
        """Stored bet ids for an address, comparing 0x-prefixed and bare hex alike."""
        key = str(bettor).lower().strip()
        if key.startswith("0x"):
            key = key[2:]
        for k in self.bettor_bets:
            kk = str(k).lower()
            if kk.startswith("0x"):
                kk = kk[2:]
            if kk == key:
                return list(self.bettor_bets[k])
        return []

    @gl.public.view
    def get_bets_by_bettor(self, bettor: str) -> typing.Any:
        out: typing.Any = []
        for bid in self._bettor_bet_ids(bettor):
            out.append(self._bet_dict(self._get_bet(bid)))
        return out

    @gl.public.view
    def get_dispute(self, dispute_id: str) -> dict:
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("Dispute not found")
        d = self.disputes[dispute_id]
        return {"id": d.id, "market_id": d.market_id, "raised_by": format(d.raised_by, "x"),
                "reason": d.reason, "resolved": d.resolved, "outcome": d.outcome}

    @gl.public.view
    def get_market_bets(self, market_id: str) -> typing.Any:
        if market_id not in self.market_bets:
            return []
        return list(self.market_bets[market_id])

    @gl.public.view
    def get_bettor_bets(self, bettor: str) -> typing.Any:
        # Stored keys come from _addr_key() (format(addr, "x")), which keeps its
        # 0x prefix — stripping only the query side never matched, so this used
        # to always return []. _bettor_bet_ids normalises both sides.
        return self._bettor_bet_ids(bettor)

    @gl.public.view
    def get_odds(self, market_id: str) -> dict:
        m = self._get_market(market_id)
        total = int(m.pool_yes) + int(m.pool_no)
        if total == 0:
            return {"yes": 50, "no": 50, "pool_yes": 0, "pool_no": 0, "total_pool": 0}
        yes_pct = int(m.pool_yes) * 100 // total
        return {"yes": yes_pct, "no": 100 - yes_pct, "pool_yes": int(m.pool_yes),
                "pool_no": int(m.pool_no), "total_pool": total}

    @gl.public.view
    def get_market_stats(self, market_id: str) -> dict:
        m = self._get_market(market_id)
        bids: typing.Any = list(self.market_bets[market_id]) if market_id in self.market_bets else []
        yc = 0
        nc = 0
        for bid in bids:
            if self.bets[bid].side == Outcome.YES:
                yc += 1
            else:
                nc += 1
        return {"market_id": market_id, "title": m.title, "status": m.status,
                "outcome": m.outcome, "pool_yes": int(m.pool_yes), "pool_no": int(m.pool_no),
                "total_bets": len(bids), "yes_bets": yc, "no_bets": nc,
                "resolver_note": m.resolver_note}

    @gl.public.view
    def get_platform_stats(self) -> dict:
        return {"total_markets": int(self.market_count), "total_volume": int(self.total_volume),
                "fee_bps": int(self.platform_fee_bps), "owner": format(self.owner, "x")}

    @gl.public.write
    def set_fee(self, bps: u256) -> bool:
        if not gl.message.sender_address == self.owner:
            raise gl.vm.UserError("Only owner")
        if not bps <= u256(500):
            raise gl.vm.UserError("Max 5 percent")
        self.platform_fee_bps = bps
        return True

    @gl.public.write
    def transfer_ownership(self, new_owner: Address) -> bool:
        if not gl.message.sender_address == self.owner:
            raise gl.vm.UserError("Only owner")
        self.owner = new_owner
        return True
