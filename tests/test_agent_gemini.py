from unittest.mock import patch, MagicMock
from models import CartContext, CartItem, BuyerMandate
import agent_gemini as agent


def _tiny_cart():
    return CartContext(session_id="s", items=[CartItem(sku="sku_009", name="Cable Organizer Kit", qty=1, price_paise=14900)])


def _big_cart():
    return CartContext(session_id="s", items=[CartItem(sku="sku_004", name="Mechanical Keyboard", qty=1, price_paise=349900)])


def test_trivial_cart_never_calls_the_llm():
    with patch.object(agent, "get_client") as mock_get:
        result = agent.decide_upsell(_tiny_cart())
        assert result.should_upsell is False
        assert "skipped" in result.reasoning
        assert mock_get.call_count == 0


def test_big_cart_calls_llm_and_parses_function_call():
    mock_call = MagicMock()
    mock_call.name = "make_upsell_decision"
    mock_call.args = {"should_upsell": True, "sku": "sku_007", "discount_pct": 10, "reasoning": "pairs well"}
    mock_response = MagicMock()
    mock_response.function_calls = [mock_call]

    with patch.object(agent, "get_client") as mock_get:
        mock_get.return_value.models.generate_content.return_value = mock_response
        result = agent.decide_upsell(_big_cart())
        assert result.should_upsell is True
        assert result.sku == "sku_007"
        assert result.discount_pct == 10


def test_malformed_llm_response_fails_safe():
    mock_response = MagicMock()
    mock_response.function_calls = []

    with patch.object(agent, "get_client") as mock_get:
        mock_get.return_value.models.generate_content.return_value = mock_response
        result = agent.decide_upsell(_big_cart())
        assert result.should_upsell is False
        assert "did not contain a valid function call" in result.reasoning


def test_mandate_category_filter_narrows_catalog_shown_to_llm():
    mock_response = MagicMock()
    mock_response.function_calls = []
    mandate = BuyerMandate(caller_type="ai_agent", max_spend_paise=100000, allowed_categories=["services"])

    with patch.object(agent, "get_client") as mock_get:
        mock_get.return_value.models.generate_content.return_value = mock_response
        agent.decide_upsell(_big_cart(), mandate=mandate)
        call_kwargs = mock_get.return_value.models.generate_content.call_args.kwargs
        system_instruction = call_kwargs["config"].system_instruction
        assert "Extended Warranty" in system_instruction
        assert "Wireless Mouse" not in system_instruction
