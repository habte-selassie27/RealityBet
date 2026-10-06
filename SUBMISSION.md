# Portal submission copy — Intelligent Contracts track

## Title
RealityBet — AI-resolved prediction market primitive (GenLayer IC)

## Notes / Description
A single 580-line GenLayer Intelligent Contract that runs YES/NO prediction markets
settled by validator consensus over live web data. The repo is the contract plus a
direct-VM test suite — no frontend, no off-chain services.

Consensus: resolution runs in gl.eq_principle.prompt_comparative over
gl.nondet.web.get + gl.nondet.exec_prompt. The leader fetches the market's primary
source, the LLM returns {outcome, confidence, reason, sources_checked}, and validators
re-fetch and re-judge independently. The comparative rule pins BOTH `outcome` and
`confidence` to exact equality — every field that can change settlement, because the
low-confidence policy rewrites yes/no to void — while prose stays fuzzy. On top of that,
the fail-toward-void policy runs inside _fetch before the comparison, so validators
agree on the final post-policy settlement result, not on a raw reply that deterministic
code could later flip; the same idempotent normalization runs again on the consensus
output before storage. Deliberately not strict_eq on LLM text (endless failed rounds)
and deliberately not a JSON-schema check (that validates shape, not truth). All
gl.nondet calls are confined to _fetch, so every other method stays deterministic.

Failure is a first-class outcome: unparsable output, low confidence, or an unknown
outcome string all rewrite to VOID, which refunds every bettor in full. A confidently
wrong settlement is the expensive failure, so the contract prefers refunds. The AI
decides; u256 integer math disposes — no LLM output ever reaches emit_transfer as an
amount. Payouts are parimutuel (gross = bet × total_pool ÷ win_pool, fee split to
owner), claimed is set before every emit_transfer including the fee leg so re-entrancy
is inert, and the guard on lock/resolve is permissionless so a market can never be held
open or unsettled past its deadline.

Settlement timing is gated so an appeal can never race a payout: claims require
status RESOLVED with no open dispute AND now >= resolved_at + 86400, while disputes
close strictly before that same instant — disjoint boundaries, so no transaction order
lets one side claim and the other side appeal against the same pool. An upheld ruling
re-stamps resolved_at and restarts the full 24h window before anything can be claimed.

State: TreeMap markets/bets/disputes with two secondary indexes (per-market and
per-bettor) so those queries are storage reads rather than full scans, readable
b<seq>-<ts> ids that need lexicographic correction for m10 vs m2, and batched
paginated views capped at 50. Lifecycle OPEN → LOCKED → RESOLVED/DISPUTED/VOIDED with
a 24h bettor-only dispute window, AI re-resolution, and owner override as last resort.

19 direct-VM tests (mock_web/mock_llm, no network) cover creation, categories, betting,
both payout directions, fee arithmetic, void and low-confidence auto-void, duplicate
claims, the claim/dispute window gate (including the boundary instant and an appeal
flipping the outcome before anything was paid), disputes, pagination ordering, and an
address-index regression. README documents the consensus choice, state design, money
math, full method reference, and the one known limitation (fund_market) explicitly
rather than hiding it.

## Evidence
- Contract source: https://github.com/habte-selassie27/RealityBet/blob/main/contracts/RealityBet.py
- Repo: https://github.com/habte-selassie27/RealityBet
- Deployed (Studio studionet): https://explorer-studio.genlayer.com/address/0x0905C1CE5680AF08354ADF09254AE86fDF66E72D
- Studio import: https://studio.genlayer.com/?import-contract=0x0905C1CE5680AF08354ADF09254AE86fDF66E72D
- Reference client (separate deployment, not in repo): https://reality-bet.vercel.app

## Reviewer talking points
- "Is this just an AI-decides-X demo?" No — the LLM returns one enum string; every
  amount, fee and refund is u256 division. Three separate ambiguity paths force VOID.
- "Why prompt_comparative and not strict_eq?" strict_eq on free-text LLM output is a
  consensus liveness bug; a format validator proves nothing about correctness. The
  comparative template is the only rule that can pin the settlement fields exactly
  (`outcome` *and* `confidence` — the ones that change what money moves) and tolerate
  the prose.
- "Is it reusable beyond a demo?" The pattern is "AI decides the settlement, chain does
  everything else", plus a full market/dispute state machine that any adjudication use
  case can lift.
