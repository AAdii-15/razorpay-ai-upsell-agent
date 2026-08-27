import sqlite3


def test_events_log_in_order_with_valid_chain(isolated_audit_db):
    import audit
    audit.log_event("s1", "agent_decision", "agent", {"x": 1}, reasoning="because")
    audit.log_event("s1", "guardrail_check", "guardrail", {"y": 2})
    audit.log_event("s1", "human_approval", "human", {"approved": True})

    trail = audit.get_trail("s1")
    assert len(trail) == 3
    assert [e["event_type"] for e in trail] == ["agent_decision", "guardrail_check", "human_approval"]

    check = audit.verify_chain("s1")
    assert check["valid"] is True
    assert check["events_checked"] == 3


def test_tampering_is_detected(isolated_audit_db):
    import audit
    audit.log_event("s1", "agent_decision", "agent", {"x": 1}, reasoning="because")
    audit.log_event("s1", "guardrail_check", "guardrail", {"y": 2})

    conn = sqlite3.connect(isolated_audit_db)
    conn.execute("UPDATE audit_log SET payload = '{\"tampered\": true}' WHERE session_id = 's1' AND event_type = 'agent_decision'")
    conn.commit()
    conn.close()

    check = audit.verify_chain("s1")
    assert check["valid"] is False
    assert check["broken_at_index"] == 0


def test_sessions_are_independent_chains(isolated_audit_db):
    import audit
    audit.log_event("s1", "agent_decision", "agent", {"x": 1})
    audit.log_event("s2", "agent_decision", "agent", {"x": 2})

    assert len(audit.get_trail("s1")) == 1
    assert len(audit.get_trail("s2")) == 1
    assert audit.verify_chain("s1")["valid"]
    assert audit.verify_chain("s2")["valid"]


def test_get_all_events_filters_by_type_across_sessions(isolated_audit_db):
    import audit
    audit.log_event("s1", "agent_decision", "agent", {"x": 1})
    audit.log_event("s2", "agent_decision", "agent", {"x": 2})
    audit.log_event("s1", "blocked", "system", {"reasons": ["x"]})

    decisions = audit.get_all_events("agent_decision")
    assert len(decisions) == 2
    blocked = audit.get_all_events("blocked")
    assert len(blocked) == 1


def test_pending_decisions_persist_across_a_simulated_restart(isolated_audit_db, monkeypatch):
    """The whole point of moving this off an in-memory dict: it must
    survive the process disappearing and a new one starting up against
    the same database file."""
    import audit
    audit.save_pending_decision("dec_1", "session_x", {"item": {"name": "Wireless Mouse"}, "final_price_paise": 116910})

    import importlib
    importlib.reload(audit)
    monkeypatch.setattr(audit, "DB_PATH", isolated_audit_db)

    result = audit.pop_pending_decision("dec_1")
    assert result is not None
    assert result["final_price_paise"] == 116910
    assert audit.pop_pending_decision("dec_1") is None
