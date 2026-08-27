"""
FastAPI orchestration layer.

Pipeline for every request: cart -> agent (LLM judgment) -> guardrails
(deterministic enforcement) -> either auto-fire Razorpay, hold for human
approval, or block -> every step written to the audit log regardless of
outcome.

Swap the LLM backend with LLM_BACKEND=claude|gemini in .env — the rest of
the pipeline doesn't know or care which one produced the suggestion.
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

from models import DecideRequest, CartContext
from mock_data import CATALOG, CART_SCENARIOS
import guardrails
import audit
import razorpay_client

load_dotenv()

LLM_BACKEND = os.environ.get("LLM_BACKEND", "gemini")
if LLM_BACKEND == "claude":
    import agent as agent_module
else:
    import agent_gemini as agent_module

app = FastAPI(title="AI Upsell Agent — Razorpay Buildathon")

PENDING_DECISIONS: dict[str, dict] = {}


@app.get("/catalog")
def get_catalog():
    return CATALOG


@app.get("/cart-scenarios")
def get_cart_scenarios():
    return CART_SCENARIOS


@app.post("/decide")
def decide(req: DecideRequest):
    cart = CartContext(session_id=req.session_id, items=req.items, customer_segment=req.customer_segment)
    decision_id = str(uuid.uuid4())

    suggestion = agent_module.decide_upsell(cart, req.mandate)
    audit.log_event(
        req.session_id, "agent_decision", "agent",
        {"decision_id": decision_id, "suggestion": suggestion.model_dump(),
         "mandate": req.mandate.model_dump() if req.mandate else None, "llm_backend": LLM_BACKEND},
        reasoning=suggestion.reasoning,
    )

    guardrail = guardrails.check_suggestion(cart.total_paise, suggestion, req.mandate)
    audit.log_event(
        req.session_id, "guardrail_check", "guardrail",
        {"decision_id": decision_id, "result": guardrail.model_dump()},
    )

    if not suggestion.should_upsell:
        return {"decision_id": decision_id, "status": "no_action", "suggestion": suggestion, "guardrail": guardrail}

    if not guardrail.approved:
        audit.log_event(req.session_id, "blocked", "system", {"decision_id": decision_id, "reasons": guardrail.reasons})
        return {"decision_id": decision_id, "status": "blocked", "suggestion": suggestion, "guardrail": guardrail}

    item = CATALOG[suggestion.sku]
    discount_applied = int(item["price_paise"] * suggestion.discount_pct / 100)
    final_price_paise = item["price_paise"] - discount_applied

    if guardrail.requires_human_approval:
        PENDING_DECISIONS[decision_id] = {
            "session_id": req.session_id, "suggestion": suggestion,
            "item": item, "final_price_paise": final_price_paise,
        }
        audit.log_event(
            req.session_id, "pending_human_approval", "system",
            {"decision_id": decision_id, "item": item["name"], "final_price_paise": final_price_paise},
        )
        return {
            "decision_id": decision_id, "status": "pending_human_approval",
            "suggestion": suggestion, "guardrail": guardrail,
            "item": item["name"], "final_price_paise": final_price_paise,
        }

    result = razorpay_client.create_payment_link(
        amount_paise=final_price_paise, description=f"Upsell: {item['name']}", session_id=req.session_id,
    )
    if result["ok"]:
        audit.log_event(
            req.session_id, "razorpay_order_created", "razorpay",
            {"decision_id": decision_id, "payment_link": result["payment_link"]["short_url"], "amount_paise": final_price_paise},
        )
        return {
            "decision_id": decision_id, "status": "auto_approved",
            "suggestion": suggestion, "guardrail": guardrail,
            "payment_link": result["payment_link"]["short_url"],
        }
    else:
        audit.log_event(
            req.session_id, "razorpay_call_failed", "razorpay",
            {"decision_id": decision_id, "error_type": result["error_type"], "error_message": result["error_message"]},
        )
        audit.log_event(
            req.session_id, "graceful_fallback", "system",
            {"decision_id": decision_id, "action": "flagged_for_manual_follow_up"},
            reasoning="Razorpay call failed; instead of crashing the request, we log this for manual follow-up and return a clear status to the caller.",
        )
        return {
            "decision_id": decision_id, "status": "failed_gracefully",
            "suggestion": suggestion, "guardrail": guardrail, "error": result,
        }


@app.post("/decide/{decision_id}/approve")
def approve(decision_id: str):
    pending = PENDING_DECISIONS.pop(decision_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="decision not found or already resolved")

    audit.log_event(pending["session_id"], "human_approval", "human", {"decision_id": decision_id, "approved": True})

    result = razorpay_client.create_payment_link(
        amount_paise=pending["final_price_paise"],
        description=f"Upsell: {pending['item']['name']}",
        session_id=pending["session_id"],
    )
    if result["ok"]:
        audit.log_event(
            pending["session_id"], "razorpay_order_created", "razorpay",
            {"decision_id": decision_id, "payment_link": result["payment_link"]["short_url"]},
        )
        return {"status": "approved_and_created", "payment_link": result["payment_link"]["short_url"]}
    else:
        audit.log_event(pending["session_id"], "razorpay_call_failed", "razorpay", {"decision_id": decision_id, "error": result})
        return {"status": "approved_but_razorpay_failed", "error": result}


@app.post("/decide/{decision_id}/reject")
def reject(decision_id: str):
    pending = PENDING_DECISIONS.pop(decision_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="decision not found or already resolved")
    audit.log_event(pending["session_id"], "human_rejection", "human", {"decision_id": decision_id, "approved": False})
    return {"status": "rejected"}


@app.get("/audit/{session_id}")
def get_audit(session_id: str):
    trail = audit.get_trail(session_id)
    verification = audit.verify_chain(session_id)
    return {"session_id": session_id, "events": trail, "chain_verification": verification}


@app.post("/demo/simulate-failure")
def simulate_failure(session_id: str = "failure_demo"):
    result = razorpay_client.create_payment_link(
        amount_paise=100, description="failure demo", session_id=session_id, simulate_failure=True,
    )
    audit.log_event(
        session_id, "razorpay_call_failed", "razorpay",
        {"error_type": result.get("error_type"), "error_message": result.get("error_message")},
        reasoning="deliberately triggered for the failure-mode demo",
    )
    audit.log_event(
        session_id, "graceful_fallback", "system",
        {"action": "flagged_for_manual_follow_up"},
        reasoning="Razorpay call failed; instead of crashing, we log this for manual follow-up and continue serving the customer normally.",
    )
    return {
        "status": "failed_gracefully",
        "razorpay_error": result,
        "recovery_action": "flagged_for_manual_follow_up",
        "note": "checkout flow was NOT interrupted for the customer",
    }
