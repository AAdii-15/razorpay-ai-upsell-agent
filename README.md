# AI Upsell Agent — Razorpay AI Buildathon (Track 01: AI Growth & Agentic Commerce)

An AI agent that decides whether to suggest an upsell at checkout, then hands that decision through a deterministic safety layer before any money moves — with a stricter safety bar for purchases initiated by *another AI agent* than for a human customer.

## The core idea

Most "AI upsell agent" submissions will be a chatbot that recommends a product. This is that, plus one more thing: **the same pipeline serves both a human checkout and an autonomous AI buyer agent**, and it does not trust either one blindly.

- A **human customer's** cart goes through merchant policy alone.
- An **AI buyer agent** must declare a *mandate* — its own spending cap and allowed categories — before it can transact. The system enforces the *stricter* of the merchant's policy and the agent's mandate, and AI-initiated purchases are held to a **lower auto-approval threshold** than human ones, because nothing is watching the transaction happen in real time on that path.

This maps directly to the track's published bar: *"Every money action explainable, bounded and gated. Show the audit trail and one failure handled gracefully."*

## Architecture
app/
├── main.py FastAPI orchestration: cart -> agent -> guardrails -> Razorpay -> audit
├── agent_gemini.py LLM reasoning (Gemini). The ONLY place an LLM makes a judgment call.
├── agent.py Same interface, Claude backend — swap via LLM_BACKEND in .env
├── guardrails.py Deterministic, hard-coded limits. The LLM cannot see or influence this file.
├── audit.py Tamper-evident, hash-chained SQLite log. Every step, always.
├── razorpay_client.py Razorpay Test Mode integration, non-throwing error handling
├── mock_data.py Demo merchant catalog + cart scenarios
└── models.py Pydantic schemas

**Why this shape:** guardrails.py is the actual enforcement — not a prompt instruction the model could ignore, drift from, or be talked out of. It is plain Python that runs after every LLM call and rejects anything outside policy, regardless of how convincing the model's reasoning was. This is deliberate: LLM output is a *proposal*, never an *authorization*.

## The AI judgment call, made explicit

Trivial carts (below ₹200) never reach the LLM at all — `agent_gemini.py::should_call_agent()` short-circuits with a static rule. Paying for an LLM call to re-derive "don't upsell a tiny cart" is wasted latency and cost. This is the "know when not to use AI" decision, made visible in code rather than left as an implicit default.

## Safety & control

| Rule | Value | Why |
|---|---|---|
| Max discount | 20% | agent can't discount its way to a "yes" |
| Max upsell price | ₹1,500 | absolute ceiling regardless of caller |
| Human-approval threshold (human customer) | ≥ ₹300 | above this, a person signs off before Razorpay is called |
| Human-approval threshold (AI agent) | ≥ ₹150 | **stricter** — no human is watching in real time on this path |
| Buyer mandate | caller-declared | AI agents can only spend within their own declared cap/categories, enforced server-side, not trusted from the prompt |
| Unknown SKU | always rejected | catches a hallucinated product before it goes anywhere near payment |

None of these live in the prompt. All of them are enforced in `guardrails.py`, after the LLM has already responded, so a hallucinated or over-eager suggestion cannot become a real transaction.

## Audit trail

Every event — agent reasoning, guardrail verdict, human approval/rejection, Razorpay call, failure, recovery — is written to an append-only SQLite log where each row's hash chains to the previous row (`audit.py`). `verify_chain(session_id)` recomputes the whole chain and will detect if any row was altered or deleted after the fact. This was tested directly: a row was corrupted in place, and `verify_chain` caught it immediately (`smoke_test.py`).

`GET /audit/{session_id}` returns the full trail plus the verification result for any session.

## Failure handling

`POST /demo/simulate-failure` deliberately sends Razorpay an invalid payload (negative amount). The call fails, the failure is logged, and the system logs a graceful fallback (flag for manual follow-up) instead of crashing the request — the caller always gets a clean `200` with a clear status, never an unhandled exception. Verified against Razorpay's real Test Mode API, not mocked.

## Running it

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, GEMINI_API_KEY (or ANTHROPIC_API_KEY + LLM_BACKEND=claude)
uvicorn app.main:app --reload --port 8000
```

Interactive API docs: `http://localhost:8000/docs`

## Example: same suggestion, two different real outcomes

Both of these are real runs against the live system, not fabricated for the README:

**Human customer, ₹3,499 keyboard in cart** → Gemini suggests a ₹1,299 wireless mouse → guardrail routes it to human approval (over the ₹300 human threshold) → approved → real Razorpay Test Mode payment link created.

**AI buyer agent, same cart, same suggestion, but a ₹200 mandate cap** → guardrail blocks it outright: *"item price ₹1,299.00 exceeds buyer agent's declared mandate cap ₹200.00."* Same LLM call, same product, different caller, different real outcome — because the system checks who's asking, not just what's being asked for.

## What I'd build next with more time

- Persist `PENDING_DECISIONS` (currently in-memory) to survive a restart
- A daily/rolling spend cap across all auto-approved transactions, not just per-transaction limits
- A standalone script that plays the autonomous AI buyer end-to-end against this API, rather than the mandate being passed as a JSON field
