"""
Gemini-powered upsell reasoning (free-tier alternative to agent.py/Claude).

Same external interface as the Claude version: decide_upsell(cart, mandate)
-> UpsellSuggestion. guardrails.py, audit.py, and main.py don't know or
care which LLM produced the suggestion — this is a swappable backend.
"""

import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

from mock_data import CATALOG, UPSELL_ALLOWED_CATEGORIES
from models import UpsellSuggestion, CartContext, BuyerMandate

load_dotenv()

# Free-tier Flash model. If this ID 404s on your account/region, open
# aistudio.google.com, check which Flash model it actually offers you,
# and set GEMINI_MODEL in .env to override.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MIN_CART_VALUE_FOR_AGENT_PAISE = 20000  # ₹200 — below this, skip the LLM call entirely

_client = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=15000),
        )
    return _client


UPSELL_FUNCTION = types.FunctionDeclaration(
    name="make_upsell_decision",
    description="Decide whether to suggest an upsell for this cart, and if so, which catalog item and why.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "should_upsell": {"type": "boolean"},
            "sku": {"type": ["string", "null"], "description": "Catalog SKU to suggest, or null if should_upsell is false"},
            "discount_pct": {"type": "integer", "description": "Suggested discount percent, 0 if none"},
            "reasoning": {"type": "string", "description": "Why this suggestion makes sense for this cart, or why no upsell is warranted"},
        },
        "required": ["should_upsell", "reasoning"],
    },
)


def should_call_agent(cart: CartContext) -> bool:
    """Deterministic pre-filter — the explicit 'know when not to use AI' rule."""
    return cart.total_paise >= MIN_CART_VALUE_FOR_AGENT_PAISE


def _catalog_for_prompt(mandate: BuyerMandate | None) -> dict:
    allowed = set(UPSELL_ALLOWED_CATEGORIES)
    if mandate and mandate.allowed_categories:
        allowed &= set(mandate.allowed_categories)
    catalog = {sku: item for sku, item in CATALOG.items() if item["category"] in allowed}
    if mandate and mandate.max_spend_paise is not None:
        catalog = {sku: item for sku, item in catalog.items() if item["price_paise"] <= mandate.max_spend_paise}
    return catalog


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
            reasoning="no catalog items fit within the buyer mandate's allowed categories and price cap",
        )

    if not mandate or mandate.caller_type == "human_customer":
        caller_desc = "a human customer at checkout"
    else:
        caller_desc = f"an autonomous AI purchasing agent (id: {mandate.agent_id or 'unknown'}) buying on a customer's behalf"

    system_instruction = (
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
    response = client.models.generate_content(
        model=MODEL,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(function_declarations=[UPSELL_FUNCTION])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=["make_upsell_decision"],
                )
            ),
        ),
    )

    calls = response.function_calls or []
    for call in calls:
        if call.name == "make_upsell_decision":
            data = call.args or {}
            return UpsellSuggestion(
                should_upsell=bool(data.get("should_upsell", False)),
                sku=data.get("sku"),
                discount_pct=int(data.get("discount_pct") or 0),
                reasoning=data.get("reasoning", ""),
            )

    return UpsellSuggestion(
        should_upsell=False,
        reasoning="agent response did not contain a valid function call — failing safe, no upsell",
    )
