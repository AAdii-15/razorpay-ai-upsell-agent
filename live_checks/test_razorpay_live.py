import sys
sys.path.insert(0, "app")
import razorpay_client

print("=== LIVE call #1: valid payment link (should succeed) ===")
r1 = razorpay_client.create_payment_link(
    amount_paise=14900,
    description="Cable Organizer Kit",
    session_id="live_test_1",
)
print(r1)
assert r1["ok"] is True, "Expected success — check your keys in .env if this fails"
print("Payment link URL:", r1["payment_link"]["short_url"])

print("\n=== LIVE call #2: simulated failure (invalid amount, should fail gracefully) ===")
r2 = razorpay_client.create_payment_link(
    amount_paise=100,
    description="failure demo",
    session_id="live_test_2",
    simulate_failure=True,
)
print(r2)
assert r2["ok"] is False, "Expected this call to fail — simulate_failure sends an invalid amount"

print("\nLIVE RAZORPAY INTEGRATION CONFIRMED WORKING (success path + failure path)")
