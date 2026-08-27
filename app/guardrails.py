"""
Deterministic guardrails for the upsell agent.

This is the enforcement layer for the buildathon's "Safety & Control"
requirement: every financial transaction must be bounded, explainable, and
human-gated/permissioned. These rules are plain Python — the LLM never sees
or can influence this file, so it cannot talk its way past a limit.
"""

from mock_data import UPSELL_ALLOWED_CATEGORIES, CATALOG
from models import GuardrailResult, BuyerMandate

# ---- Hard policy limits (would come from merchant config in a real system) ----
MAX_DISCOUNT_PCT = 20                          # agent can never suggest more than 20% off
MAX_UPSELL_ABS_PAISE = 1500_00                 # a single upsell can never exceed ₹1,500
MAX_UPSELL_PCT_OF_CART = 0.60                  # upsell can't exceed 60% of existing cart value

# Human-approval thresholds differ by who initiated the purchase. AI-agent-
# initiated transactions get a STRICTER (lower) auto-approve ceiling than
# human-initiated ones, because no human is watching the decision happen
# in real time on that path. Deliberate safety asymmetry, not an oversight.
HUMAN_APPROVAL_THRESHOLD_PAISE = 300_00        # human checkout: >= ₹300 needs sign-off
AI_AGENT_APPROVAL_THRESHOLD_PAISE = 150_00     # AI-agent buyer: >= ₹150 needs sign-off


def check_suggestion(cart_total_paise: int, suggestion, mandate: BuyerMandate | None = None) -> GuardrailResult:
    reasons: list[str] = []

    if not suggestion.should_upsell:
        return GuardrailResult(
            approved=True, requires_human_approval=False,
            reasons=["agent chose not to upsell — nothing to gate"],
        )

    item = CATALOG.get(suggestion.sku)
    if item is None:
        return GuardrailResult(
            approved=False, requires_human_approval=False,
            reasons=[f"unknown SKU '{suggestion.sku}' — not in catalog, suggestion rejected"],
        )

    if item["category"] not in UPSELL_ALLOWED_CATEGORIES:
        reasons.append(f"category '{item['category']}' is not in the allowed upsell list")

    if suggestion.discount_pct > MAX_DISCOUNT_PCT:
        reasons.append(f"discount {suggestion.discount_pct}% exceeds max allowed {MAX_DISCOUNT_PCT}%")

    price = item["price_paise"]
    if price > MAX_UPSELL_ABS_PAISE:
        reasons.append(f"item price Rs.{price/100:.2f} exceeds absolute cap Rs.{MAX_UPSELL_ABS_PAISE/100:.2f}")

    if cart_total_paise > 0 and price > cart_total_paise * MAX_UPSELL_PCT_OF_CART:
        reasons.append(f"upsell price exceeds {int(MAX_UPSELL_PCT_OF_CART * 100)}% of cart value")

    # --- Buyer mandate checks (only apply when the caller declared one) ---
    if mandate is not None:
        if mandate.max_spend_paise is not None and price > mandate.max_spend_paise:
            reasons.append(f"item price Rs.{price/100:.2f} exceeds buyer agent's declared mandate cap Rs.{mandate.max_spend_paise/100:.2f}")
        if mandate.allowed_categories is not None and item["category"] not in mandate.allowed_categories:
            reasons.append(f"category '{item['category']}' is outside buyer agent's declared allowed categories {mandate.allowed_categories}")

    if reasons:
        return GuardrailResult(approved=False, requires_human_approval=False, reasons=reasons)

    is_ai_agent = mandate is not None and mandate.caller_type == "ai_agent"
    threshold = AI_AGENT_APPROVAL_THRESHOLD_PAISE if is_ai_agent else HUMAN_APPROVAL_THRESHOLD_PAISE
    requires_human = price >= threshold

    if requires_human:
        who = "AI-agent-initiated" if is_ai_agent else "human-initiated"
        note = f"within policy, routed to human approval ({who}, price >= Rs.{threshold/100:.2f} threshold)"
    else:
        note = "within policy, auto-approved"

    return GuardrailResult(approved=True, requires_human_approval=requires_human, reasons=[note])
