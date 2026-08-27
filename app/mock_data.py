"""
Mock merchant catalog and cart scenarios.
Represents a small D2C electronics/accessories store — the "merchant" our
upsell agent works on behalf of. All prices in paise (Razorpay's base unit).
"""

CATALOG = {
    "sku_001": {"name": "Wireless Earbuds Pro", "category": "audio", "price_paise": 249900},
    "sku_002": {"name": "USB-C Fast Charger 65W", "category": "accessories", "price_paise": 149900},
    "sku_003": {"name": "Laptop Sleeve 14-inch", "category": "accessories", "price_paise": 79900},
    "sku_004": {"name": "Mechanical Keyboard", "category": "peripherals", "price_paise": 349900},
    "sku_005": {"name": "Wireless Mouse", "category": "peripherals", "price_paise": 129900},
    "sku_006": {"name": "Extended Warranty (1yr)", "category": "services", "price_paise": 99900},
    "sku_007": {"name": "Screen Protector 2-Pack", "category": "accessories", "price_paise": 39900},
    "sku_008": {"name": "Premium Backpack", "category": "bags", "price_paise": 299900},
    "sku_009": {"name": "Cable Organizer Kit", "category": "accessories", "price_paise": 14900},
}

# Categories the agent is ALLOWED to pull upsells from.
# Enforced in guardrails.py, not just suggested in the prompt — the LLM
# cannot upsell outside this list even if it tries to.
UPSELL_ALLOWED_CATEGORIES = {"accessories", "peripherals", "services"}

CART_SCENARIOS = {
    "cart_laptop_buyer": {
        "session_id": "cart_laptop_buyer",
        "items": [
            {"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900},
        ],
        "customer_segment": "returning_customer",
    },
    "cart_low_value": {
        "session_id": "cart_low_value",
        "items": [
            {"sku": "sku_007", "name": "Screen Protector 2-Pack", "qty": 1, "price_paise": 39900},
        ],
        "customer_segment": "new_customer",
    },
    "cart_high_value": {
        "session_id": "cart_high_value",
        "items": [
            {"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900},
            {"sku": "sku_008", "name": "Premium Backpack", "qty": 1, "price_paise": 299900},
        ],
        "customer_segment": "returning_customer",
    },
}
