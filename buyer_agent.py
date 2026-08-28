"""
Standalone autonomous AI buyer agent.

Separate process from the merchant system in app/ — it holds no imports
from app/, only speaks to the merchant over HTTP, exactly like a real
third-party shopping agent would.

This agent makes exactly ONE real LLM judgment call: translating a
natural-language shopping brief from its principal ("keep it budget
friendly, only accessories or peripherals") into a concrete, bounded
mandate (max_spend_paise, allowed_categories). That is a genuine judgment
call — turning fuzzy human intent into a strict numeric policy.

Everything AFTER that point — accept the offer, wait on human review, or
decline because the offer breaks its own mandate — is deliberately
DETERMINISTIC, not a second LLM call. Re-litigating a financial boundary
with another model invocation every time a request gets a response is
exactly the kind of decision that should NOT be delegated to an LLM: the
boundary was set once, on purpose, with reasoning attached, and the agent
doesn't get to argue itself past it after the fact. This mirrors the same
"know when not to use AI" principle applied on the merchant side
(agent_gemini.py's should_call_agent()) — now applied on the buyer side too.
"""

import argparse
import json
import os
import sys
import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

VALID_CATEGORIES = {"accessories", "peripherals", "services"}

MANDATE_TOOL = types.FunctionDeclaration(
    name="set_shopping_mandate",
    description="Convert a natural-language shopping brief into a concrete, bounded spending mandate.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "max_spend_paise": {"type": "integer", "description": "Maximum this agent may authorize for a single upsell, in paise. Be conservative if the brief is ambiguous."},
            "allowed_categories": {
                "type": "array", "items": {"type": "string"},
                "description": "Subset of ['accessories', 'peripherals', 'services'] this agent may consider, based on the brief.",
            },
            "reasoning": {"type": "string", "description": "Why this specific cap and category set, given the brief."},
        },
        "required": ["max_spend_paise", "allowed_categories", "reasoning"],
    },
)


def log(msg: str) -> None:
    print(f"[buyer-agent] {msg}")


def derive_mandate_from_brief(brief: str, gemini_api_key: str, model: str) -> dict:
    """The one real LLM call this agent makes."""
    client = genai.Client(api_key=gemini_api_key, http_options=types.HttpOptions(timeout=15000))
    response = client.models.generate_content(
        model=model,
        contents=f'Shopping brief from my principal: "{brief}"\n\nConvert this into a concrete spending mandate.',
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are an autonomous shopping agent's policy interpreter. Convert a "
                "natural-language brief into a strict numeric spending mandate for a SINGLE "
                "upsell item. Be conservative: if the brief is ambiguous about budget, pick a "
                "cautious number rather than a generous one. allowed_categories must be a "
                "subset of: accessories, peripherals, services — nothing else exists in this "
                "merchant's catalog."
            ),
            tools=[types.Tool(function_declarations=[MANDATE_TOOL])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=["set_shopping_mandate"],
                )
            ),
        ),
    )

    calls = response.function_calls or []
    for call in calls:
        if call.name == "set_shopping_mandate":
            data = dict(call.args)
            data["allowed_categories"] = [c for c in data.get("allowed_categories", []) if c in VALID_CATEGORIES]
            return data

    raise RuntimeError("mandate interpreter returned no function call — failing safe, not shopping")


def run_scenario(base_url: str, agent_id: str, session_id: str, items: list[dict],
                  customer_segment: str, api_key: str | None, mandate_data: dict) -> None:
    log(f"--- shopping session '{session_id}' ---")
    log(f"LLM-derived mandate: max_spend=₹{mandate_data['max_spend_paise']/100:.2f}, categories={mandate_data['allowed_categories']}")
    log(f"interpreter's reasoning: \"{mandate_data['reasoning']}\"")

    payload = {
        "session_id": session_id,
        "items": items,
        "customer_segment": customer_segment,
        "mandate": {
            "caller_type": "ai_agent",
            "max_spend_paise": mandate_data["max_spend_paise"],
            "allowed_categories": mandate_data["allowed_categories"],
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

    if suggestion is not None:
        log(f"merchant's agent proposed: should_upsell={suggestion['should_upsell']}, sku={suggestion.get('sku')}, reasoning=\"{suggestion['reasoning']}\"")
    else:
        log("merchant's agent could not be reached to make a suggestion at all.")

    if status == "no_action":
        log("decision: nothing offered, nothing to do. session complete.")
    elif status == "auto_approved":
        log(f"decision: offer was within my mandate and auto-approved. payment link: {data['payment_link']}")
        log("taking the payment link on my principal's behalf. session complete.")
    elif status == "pending_human_approval":
        log(f"decision: offer requires the MERCHANT's human to sign off (decision_id={data['decision_id']}).")
        log("I cannot self-approve this — no autonomous agent auto-clears a human-gated transaction. Waiting, not forcing it.")
    elif status == "blocked":
        reasons = data["guardrail"]["reasons"]
        mandate_violation = any("mandate cap" in r or "allowed categories" in r for r in reasons)
        if mandate_violation:
            log(f"decision: offer was rejected — it violates MY OWN declared mandate: {reasons}")
            log("declining cleanly rather than retrying with a looser budget I didn't actually authorize. session complete, no purchase made.")
        else:
            log(f"decision: offer was rejected by merchant policy (not my mandate): {reasons}")
            log("nothing further to do — this is the merchant's own limit, not mine to override. session complete.")
    elif status == "failed_gracefully":
        error = data.get("error", {})
        log(f"decision: offer was valid and approved, but the merchant's payment system failed to process it ({error.get('error_type')}: {error.get('error_message')}).")
        log("this is a merchant-side system failure, not a policy rejection — not retrying automatically, logging and moving on. session complete, no purchase made.")
    elif status == "agent_call_failed":
        error = data.get("error", {})
        log(f"decision: the merchant's own AI reasoning was unavailable ({error.get('error_message')}) — no suggestion was even made.")
        log("not retrying automatically — logging and moving on. session complete, no purchase made.")
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
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY"))
    parser.add_argument("--gemini-model", default=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"))
    args = parser.parse_args()

    if not args.gemini_api_key:
        log("ERROR: no Gemini API key found (set GEMINI_API_KEY in .env or pass --gemini-api-key)")
        sys.exit(1)

    log(f"starting autonomous shopping run against {args.base_url}")
    log("this is a separate process from the merchant — it only knows the merchant's public API, nothing else")

    keyboard_cart = [{"sku": "sku_004", "name": "Mechanical Keyboard", "qty": 1, "price_paise": 349900}]

    scenarios = [
        {
            "session_id": "buyer_agent_run_generous_brief",
            "items": keyboard_cart,
            "customer_segment": "returning_customer",
            "brief": "I'm building out a full desk setup and I'm happy to spend on quality accessories or peripherals — up to about fifteen hundred rupees for a single add-on.",
        },
        {
            "session_id": "buyer_agent_run_tight_brief",
            "items": keyboard_cart,
            "customer_segment": "returning_customer",
            "brief": "I just want the keyboard. Keep any add-on spend minimal — nothing over two hundred rupees, accessories or peripherals only.",
        },
    ]

    for s in scenarios:
        log(f"interpreting shopping brief: \"{s['brief']}\"")
        try:
            mandate_data = derive_mandate_from_brief(s["brief"], args.gemini_api_key, args.gemini_model)
        except Exception as e:
            log(f"mandate interpretation failed: {e} — skipping this session rather than shopping unbounded")
            print()
            continue

        run_scenario(
            base_url=args.base_url,
            agent_id=args.agent_id,
            session_id=s["session_id"],
            items=s["items"],
            customer_segment=s["customer_segment"],
            api_key=args.api_key,
            mandate_data=mandate_data,
        )
        print()

    log("all shopping sessions complete.")


if __name__ == "__main__":
    main()
