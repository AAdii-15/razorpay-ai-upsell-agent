from pydantic import BaseModel
from typing import Optional, Literal


class CartItem(BaseModel):
    sku: str
    name: str
    qty: int
    price_paise: int


class CartContext(BaseModel):
    session_id: str
    items: list[CartItem]
    customer_segment: str = "unknown"

    @property
    def total_paise(self) -> int:
        return sum(i.price_paise * i.qty for i in self.items)


class UpsellSuggestion(BaseModel):
    should_upsell: bool
    sku: Optional[str] = None
    name: Optional[str] = None
    discount_pct: int = 0
    reasoning: str


class BuyerMandate(BaseModel):
    """
    Declares who is buying and under what constraints. A human checkout
    session typically has no mandate (merchant policy alone governs). An
    autonomous AI buyer agent MUST declare one — its own spending cap and
    allowed categories — and guardrails.py enforces the stricter of the
    mandate and merchant policy, never the looser one.
    """
    caller_type: Literal["human_customer", "ai_agent"] = "human_customer"
    max_spend_paise: Optional[int] = None
    allowed_categories: Optional[list[str]] = None
    agent_id: Optional[str] = None


class GuardrailResult(BaseModel):
    approved: bool
    requires_human_approval: bool
    reasons: list[str]


class AgentDecisionResponse(BaseModel):
    session_id: str
    suggestion: UpsellSuggestion
    guardrail: GuardrailResult
    decision_id: str
    status: Literal[
        "auto_approved",
        "pending_human_approval",
        "blocked",
        "skipped_no_llm_call",
    ]


class DecideRequest(BaseModel):
    session_id: str
    items: list[CartItem]
    customer_segment: str = "unknown"
    mandate: Optional[BuyerMandate] = None
