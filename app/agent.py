"""
Claude-powered upsell reasoning.

This is the ONLY place in the codebase where an LLM makes a judgment call.
Everything downstream (guardrails, human gating, Razorpay calls) is
deterministic code that does not trust this output blindly.

Design decision (documented, not accidental): trivial carts skip the LLM
call entirely — see should_call_agent() below. Paying for an LLM call to
re-derive "don't upsell a tiny single-item cart" is wasted latency and
cost. That is the "know when not to use AI" judgment call, made explicit
in code rather than left implicit.
"""

import os
import json
from anthropic import Anthropic
from dotenv import load_dotenv

from mock_data import CATALOG, UPSELL_ALLOWED_CATEGORIES
from models import UpsellSuggestion, CartContext, BuyerMandate

load_dotenv()

MODEL = "claude-sonnet-4-6"
MIN_CART_VALUE_FOR_AGENT_PAISE = 20000  # ₹200 — below this, skip the LLM call entirely

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


UPSELL_TOOL = {
    "name": "make_upsell_decision",
    "description": "Decide whether to suggest an upsell for this cart, and if so, which catalog item and why.",
    "input_schema": {
        "type": "object",
        "properties": {
            "should_upsell": {"type": "boolean"},
            "sku": {"type": ["string", "null"], "description": "Catalog SKU to suggest, or null if should_upsell is false"},
            "discount_pct": {"type": "integer", "description": "Suggested discount percent, 0 if none"},
            "reasoning": {"type": "string", "description": "Why this suggestion makes sense for this cart, or why no upsell is warranted"},
        },
        "required": ["should_upsell", "reasoning"],
    },
}


def should_call_agent(cart: CartContext) -> bool:
    """Deterministic pre-filter — the explicit 'when NOT to use AI' rule."""
    return cart.total_paise >= MIN_CART_VALUE_FOR_AGENT_PAISE


def _catalog_for_prompt(mandate: BuyerMandate | None) -> dict:
    allowed = set(UPSELL_ALLOWED_CATEGORIES)
    if mandate and mandate.allowed_categories:
        allowed &= set(mandate.allowed_categories)
    return {sku: item for sku, item in CATALOG.items() if item["category"] in allowed}


def decide_upsell(cart: CartContext, mandate: BuyerMandate | None = None) -> UpsellSuggestion:
    if not should_call_agent(cart):
        return UpsellSuggestion(
            should_upsell=False,
            reasoning=(
                f"cart value below ₹{MIN_CART_VALUE_FOR_AGENT_PAISE/100:.0f} minimum — "
                "skipped the LLM call by policy, not worth the cost/latency for a trivial cart"
            ),
        )

    catalog = _catalog_for_prompt(mandate)
    if not catalog:
        return UpsellSuggestion(
            should_upsell=False,
            reasoning="no catalog items available within the buyer mandate's allowed categories",
        )

    if not mandate or mandate.caller_type == "human_customer":
        caller_desc = "a human customer at checkout"
    else:
        caller_desc = f"an autonomous AI purchasing agent (id: {mandate.agent_id or 'unknown'}) buying on a customer's behalf"

    system = (
        "You are an upsell recommendation engine for a small electronics/accessories merchant. "
        "You may ONLY recommend items from the catalog provided below — never invent a SKU. "
        "Recommend an upsell only when it genuinely complements the cart; do not upsell just to "
        "upsell. Keep discounts modest and justified. Be honest in your reasoning, including "
        "saying so plainly if no upsell is a good idea for this particular cart.\n\n"
        f"Catalog (SKU: name, category, price in paise):\n{json.dumps(catalog, indent=2)}"
    )

    user_msg = (
        f"Cart belongs to {caller_desc}.\n"
        f"Items: {json.dumps([i.model_dump() for i in cart.items])}\n"
        f"Cart total: {cart.total_paise} paise\n"
        f"Customer segment: {cart.customer_segment}\n\n"
        "Decide whether to suggest an upsell."
    )

    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        tools=[UPSELL_TOOL],
        tool_choice={"type": "tool", "name": "make_upsell_decision"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "make_upsell_decision":
            data = block.input
            return UpsellSuggestion(
                should_upsell=data.get("should_upsell", False),
                sku=data.get("sku"),
                discount_pct=data.get("discount_pct", 0) or 0,
                reasoning=data.get("reasoning", ""),
            )

    return UpsellSuggestion(
        should_upsell=False,
        reasoning="agent response did not contain a valid tool call — failing safe, no upsell",
    )
