from models import UpsellSuggestion, BuyerMandate
import guardrails


def test_low_value_upsell_auto_approved():
    s = UpsellSuggestion(should_upsell=True, sku="sku_009", discount_pct=10, reasoning="cheap complement")
    g = guardrails.check_suggestion(cart_total_paise=349900, suggestion=s)
    assert g.approved and not g.requires_human_approval


def test_mid_value_upsell_requires_human_approval():
    s = UpsellSuggestion(should_upsell=True, sku="sku_007", discount_pct=5, reasoning="mid value")
    g = guardrails.check_suggestion(cart_total_paise=999900, suggestion=s)
    assert g.approved and g.requires_human_approval


def test_hallucinated_sku_rejected():
    s = UpsellSuggestion(should_upsell=True, sku="sku_does_not_exist", discount_pct=5, reasoning="hallucinated")
    g = guardrails.check_suggestion(cart_total_paise=100000, suggestion=s)
    assert not g.approved
    assert "not in catalog" in g.reasons[0]


def test_excessive_discount_rejected():
    s = UpsellSuggestion(should_upsell=True, sku="sku_002", discount_pct=50, reasoning="too generous")
    g = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s)
    assert not g.approved
    assert any("discount" in r for r in g.reasons)


def test_no_upsell_suggestion_always_approved_trivially():
    s = UpsellSuggestion(should_upsell=False, reasoning="nothing fits this cart")
    g = guardrails.check_suggestion(cart_total_paise=100000, suggestion=s)
    assert g.approved and not g.requires_human_approval


def test_ai_agent_has_stricter_approval_threshold_than_human():
    s = UpsellSuggestion(should_upsell=True, sku="sku_007", discount_pct=5, reasoning="x")
    g_human = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s, mandate=None)
    mandate = BuyerMandate(caller_type="ai_agent", max_spend_paise=50000, agent_id="bot-1")
    g_agent = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s, mandate=mandate)

    assert g_human.approved and g_human.requires_human_approval
    assert g_agent.approved and g_agent.requires_human_approval
    assert "human-initiated" in g_human.reasons[0]
    assert "AI-agent-initiated" in g_agent.reasons[0]


def test_mandate_cap_overrides_otherwise_valid_merchant_approval():
    s = UpsellSuggestion(should_upsell=True, sku="sku_007", discount_pct=5, reasoning="x")
    mandate = BuyerMandate(caller_type="ai_agent", max_spend_paise=20000, agent_id="cheap-bot")
    g = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s, mandate=mandate)
    assert not g.approved
    assert "mandate cap" in g.reasons[0]


def test_mandate_category_restriction_enforced():
    s = UpsellSuggestion(should_upsell=True, sku="sku_006", discount_pct=0, reasoning="warranty upsell")
    mandate = BuyerMandate(caller_type="ai_agent", max_spend_paise=200000, allowed_categories=["accessories"], agent_id="bot-2")
    g = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s, mandate=mandate)
    assert not g.approved
    assert "allowed categories" in g.reasons[0]


def test_daily_budget_within_cap_passes():
    within, reason = guardrails.check_daily_budget(this_amount_paise=10000, todays_auto_approved_total_paise=100000)
    assert within is True


def test_daily_budget_exceeded_fails():
    within, reason = guardrails.check_daily_budget(
        this_amount_paise=10000,
        todays_auto_approved_total_paise=guardrails.DAILY_AUTO_APPROVE_BUDGET_PAISE,
    )
    assert within is False
    assert "daily auto-approve budget" in reason
