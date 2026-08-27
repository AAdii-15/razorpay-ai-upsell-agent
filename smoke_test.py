import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

db_path = os.path.join(os.path.dirname(__file__), "audit.db")
if os.path.exists(db_path):
    os.remove(db_path)

from models import UpsellSuggestion
import guardrails
import audit

print("=== Scenario 1: valid, low-value upsell -> should auto-approve ===")
s1 = UpsellSuggestion(should_upsell=True, sku="sku_009", name="Cable Organizer Kit", discount_pct=10, reasoning="pairs with keyboard purchase")
g1 = guardrails.check_suggestion(cart_total_paise=349900, suggestion=s1)
print(g1)
assert g1.approved and not g1.requires_human_approval

print("\n=== Scenario 2: valid, mid-value upsell -> should require human approval ===")
s2 = UpsellSuggestion(should_upsell=True, sku="sku_007", name="Screen Protector 2-Pack", discount_pct=5, reasoning="mid-value complement, over auto-approve threshold")
g2 = guardrails.check_suggestion(cart_total_paise=999900, suggestion=s2)
print(g2)
assert g2.approved and g2.requires_human_approval

print("\n=== Scenario 3: hallucinated SKU -> should be rejected ===")
s3 = UpsellSuggestion(should_upsell=True, sku="sku_999", name="Fake Product", discount_pct=5, reasoning="made up")
g3 = guardrails.check_suggestion(cart_total_paise=100000, suggestion=s3)
print(g3)
assert not g3.approved

print("\n=== Scenario 4: discount too aggressive -> should be rejected ===")
s4 = UpsellSuggestion(should_upsell=True, sku="sku_002", name="USB-C Fast Charger 65W", discount_pct=50, reasoning="trying to force a sale")
g4 = guardrails.check_suggestion(cart_total_paise=500000, suggestion=s4)
print(g4)
assert not g4.approved

print("\n=== Audit log + hash chain ===")
session = "smoke_test_session"
audit.log_event(session, "agent_decision", "agent", {"suggestion": s1.model_dump()}, reasoning=s1.reasoning)
audit.log_event(session, "guardrail_check", "guardrail", {"result": g1.model_dump()})
audit.log_event(session, "human_approval", "human", {"approved": True})
trail = audit.get_trail(session)
print(f"events logged: {len(trail)}")
for e in trail:
    print(f"  [{e['actor']}] {e['event_type']} hash={e['hash'][:12]}...")

check = audit.verify_chain(session)
print("verify_chain (should be untampered):", check)
assert check["valid"]

print("\n=== Tamper detection test ===")
conn = sqlite3.connect(db_path)
conn.execute("UPDATE audit_log SET payload = '{\"tampered\": true}' WHERE session_id = ? AND event_type = 'agent_decision'", (session,))
conn.commit()
conn.close()
check2 = audit.verify_chain(session)
print("verify_chain (after tampering, should be INVALID):", check2)
assert not check2["valid"]

print("\nALL SMOKE TESTS PASSED")
