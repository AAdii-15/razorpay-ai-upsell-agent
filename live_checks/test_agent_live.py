import sys
sys.path.insert(0, "app")
from models import CartContext, CartItem, BuyerMandate
import agent

print("=== LIVE: big cart, human customer (real Claude call) ===")
cart = CartContext(
    session_id="live_agent_test_1",
    items=[CartItem(sku="sku_004", name="Mechanical Keyboard", qty=1, price_paise=349900)],
    customer_segment="returning_customer",
)
result = agent.decide_upsell(cart)
print(result)
assert isinstance(result.reasoning, str) and len(result.reasoning) > 0

print("\n=== LIVE: same cart, but an AI buyer agent with a tight mandate ===")
mandate = BuyerMandate(caller_type="ai_agent", max_spend_paise=20000, agent_id="demo-buyer-bot")
result2 = agent.decide_upsell(cart, mandate=mandate)
print(result2)

print("\n=== LIVE: trivial cart -> should NOT call the API at all ===")
tiny_cart = CartContext(session_id="live_agent_test_2", items=[CartItem(sku="sku_009", name="Cable Organizer Kit", qty=1, price_paise=14900)])
result3 = agent.decide_upsell(tiny_cart)
print(result3)
assert result3.should_upsell is False and "skipped" in result3.reasoning

print("\nLIVE AGENT TEST DONE — read the reasoning above and sanity check it makes sense")
