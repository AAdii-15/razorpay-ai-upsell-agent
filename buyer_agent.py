"""
Standalone autonomous AI buyer agent.

This is a SEPARATE process from the merchant system in app/ — it knows
nothing about guardrails.py, audit.py, or agent_gemini.py internally. It
only speaks to the merchant over HTTP, exactly like a real third-party
shopping agent would (an ACP/AP2-style buyer). This is the concrete
demonstration of the "agent-to-agent commerce" claim: two independent
programs, one server boundary between them, real requests.
"""

import argparse
import os
import sys
import requests


def log(msg: str) -> None:
    print(f"[buyer-agent] {msg}")


def run_scenario(base_url: str, agent_id: str, max_spend_paise: int, allowed_categories: list[str],
                  session_id: str, items: list[dict], customer_segment: str, api_key: str | None) -> None:
    log(f"--- shopping session '{session_id}' ---")
    log(f"my mandate: max_spend=₹{max_spend_paise/100:.2f}, allowed_categories={allowed_categories}")

    payload = {
        "session_id": session_id,
        "items": items,
        "customer_segment": customer_segment,
        "mandate": {
            "caller_type": "ai_agent",
            "max_spend_paise": max_spend_paise,
            "allowed_categories": allowed_categories,
            "agent_id": agent_id,
        },
    }

    headers = {"X-API-Key": api_key} if api_key else {}

    try:
        resp = requests.post(f"{base_url}/decide", json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"merchant API unreachable or errored: {e} — aborting this session, not retrying blindly")
        return

    data = resp.json()
    status = data["status"]
    suggestion = data["suggestion"]

    log(f"merchant's agent proposed: should_upsell={suggestion['should_upsell']}, sku={suggestion.get('sku')}, reasoning=\"{suggestion['reasoning']}\"")

    if status == "no_action":
        log("decision: nothing offered, nothing to do. session complete.")

    elif status == "auto_approved":
        log(f"decision: offer was within my mandate and auto-approved by the merchant. payment link: {data['payment_link']}")
        log("taking the payment link on my principal's behalf. session complete.")

    elif status == "pending_human_approval":
        log(f"decision: offer requires the MERCHANT's human to sign off (decision_id={data['decision_id']}).")
        log("I cannot self-approve this — by design, no autonomous agent auto-clears a human-gated transaction. Waiting, not forcing it.")

    elif status == "blocked":
        reasons = data["guardrail"]["reasons"]
        mandate_violation = any("mandate cap" in r or "allowed categories" in r for r in reasons)
        if mandate_violation:
            log(f"decision: offer was rejected — it violates MY OWN declared mandate: {reasons}")
            log("declining cleanly rather than retrying with a looser budget I didn't actually authorize. session complete, no purchase made.")
        else:
            log(f"decision: offer was rejected by merchant policy (not my mandate): {reasons}")
            log("nothing further to do — this is the merchant's own limit, not mine to override. session complete.")

    else:
        log(f"unexpected status '{status}' — failing safe, not acting on it.")

    log(f"pulling my own audit trail for '{session_id}' to verify what actually happened...")
    try:
        audit_resp = requests.get(f"{base_url}/audit/{session_id}", timeout=15)
        audit_resp.raise_for_status()
        audit_data = audit_resp.json()
        log(f"audit trail: {len(audit_data['events'])} events, chain valid = {audit_data['chain_verification']['valid']}")
    except requests.RequestException as e:
        log(f"could not fetch audit trail: {e}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous AI buyer agent demo")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--agent-id", default="demo-buyer-bot-1")
    parser.add_argument("--api-key", default=os.environ.get("MERCHANT_API_KEY"))
    args = parser.parse_args()

    log(f"starting autonomous shopping run against {args.base_url}")
    log("this is a separate process from the merchant — it only knows the merchant's public API, nothing else")

    scenarios = [
        {
            "session_id": "buyer_agent_run_generous_mandate",
            "items": [{"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900}],
            "customer_segment": "returning_customer",
            "max_spend_paise": 150000,
            "allowed_categories": ["accessories", "peripherals", "services"],
        },
        {
            "session_id": "buyer_agent_run_tight_mandate",
            "items": [{"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900}],
            "customer_segment": "returning_customer",
            "max_spend_paise": 20000,
            "allowed_categories": ["accessories", "peripherals", "services"],
        },
    ]

    for s in scenarios:
        run_scenario(
            base_url=args.base_url,
            agent_id=args.agent_id,
            max_spend_paise=s["max_spend_paise"],
            allowed_categories=s["allowed_categories"],
            session_id=s["session_id"],
            items=s["items"],
            customer_segment=s["customer_segment"],
            api_key=args.api_key,
        )
        print()

    log("all shopping sessions complete.")


if __name__ == "__main__":
    main()
