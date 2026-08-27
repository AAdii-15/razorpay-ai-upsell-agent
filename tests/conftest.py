import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest


@pytest.fixture
def isolated_audit_db(tmp_path, monkeypatch):
    """Every test gets its own empty audit.db — no cross-test pollution,
    no dependency on run order, and nothing touches the real audit.db a
    developer might be looking at while running these."""
    import audit
    test_db = tmp_path / "test_audit.db"
    monkeypatch.setattr(audit, "DB_PATH", test_db)
    yield test_db
