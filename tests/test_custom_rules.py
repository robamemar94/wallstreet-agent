import pytest
from fastapi.testclient import TestClient
from main import app
from app.infrastructure.db.database import SessionLocal
from app.infrastructure.dependencies import get_settings_from_db, DEFAULT_SETTINGS
from agents.utils import create_agent_and_task
from unittest.mock import patch, MagicMock

client = TestClient(app)

def test_settings_api_saves_custom_rules():
    # 1. Fetch current settings first
    response = client.get("/settings")
    assert response.status_code == 200

    # 2. Update with a custom evaluation rule
    payload = {
        "ui": {"items_per_page": 12, "dark_mode": True},
        "valuation": {"ganga": 0.55, "barata": 0.80, "justo_max": 1.10, "cara_max": 1.35},
        "performance": {"benchmark_ticker": "SPY", "cache_duration_min": 30},
        "brokers": {
            "degiro_fee_eur": 1.5,
            "degiro_fee_usd": 1.5,
            "degiro_fee_sek": 4.0,
            "degiro_autofx_pct": 0.3,
            "ibkr_fee_usd_per_share": 0.006,
            "ibkr_fee_usd_min": 1.10,
            "ibkr_fee_usd_max_pct": 1.1,
            "ibkr_autofx_usd_min": 2.10,
            "ibkr_autofx_usd_pct": 0.22,
            "ibkr_fee_sek_min": 41.0,
            "ibkr_fee_sek_pct": 0.06,
            "ibkr_fee_eur_min": 1.30,
            "ibkr_fee_eur_pct": 0.06
        },
        "custom_evaluation_rules": [
            {"title": "Norma Test 1", "content": "Contenido test 1."},
            {"title": "Norma Test 2", "content": "Contenido test 2."}
        ]
    }

    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # 3. Query the DB directly to make sure they are stored
    db = SessionLocal()
    try:
        db_settings = get_settings_from_db(db)
        assert isinstance(db_settings["custom_evaluation_rules"], list)
        assert db_settings["custom_evaluation_rules"][0]["title"] == "Norma Test 1"
        assert db_settings["custom_evaluation_rules"][1]["content"] == "Contenido test 2."
        assert db_settings["ui"]["items_per_page"] == 12
    finally:
        db.close()


def test_get_settings_from_db_merging():
    db = SessionLocal()
    try:
        db_settings = get_settings_from_db(db)
        # Ensure that even if we query, other DEFAULT_SETTINGS keys are present (merged correctly)
        assert "labels" in db_settings
        assert db_settings["labels"]["ganga"] == "Ganga"
    finally:
        db.close()


@patch("agents.utils.execute_crew")
@patch("agents.utils.get_agent")
@patch("agents.utils.create_task")
def test_create_agent_and_task_injects_rules(mock_create_task, mock_get_agent, mock_execute_crew):
    mock_execute_crew.return_value = "Mocked execution results"
    mock_agent = MagicMock()
    mock_get_agent.return_value = mock_agent
    
    format_args = {
        "current_date": "Friday",
        "target_name": "Apple",
        "ticker": "AAPL",
        "audit_context": "Some financial tables"
    }

    # Execute the agent creation facade for rules_auditor
    res = create_agent_and_task("rules_auditor", "rules_auditor_task", format_args)
    assert res == "Mocked execution results"

    # Ensure format_args got injected with custom_evaluation_rules as compiled Markdown string
    assert "custom_evaluation_rules" in format_args
    assert "### Norma Test 1\nContenido test 1." in format_args["custom_evaluation_rules"]
    assert "### Norma Test 2\nContenido test 2." in format_args["custom_evaluation_rules"]


def test_get_settings_from_db_migration_legacy_rules():
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.db.models import DBSetting
    from app.infrastructure.dependencies import get_settings_from_db
    
    db = SessionLocal()
    try:
        # Backup original value if exists
        original_setting = db.query(DBSetting).filter(DBSetting.key == "custom_evaluation_rules").first()
        original_value = original_setting.value if original_setting else None
        
        # Simulate legacy settings in database (11 items)
        legacy_rules = [{"title": f"Norma {i}", "content": f"Contenido legacy {i}"} for i in range(1, 12)]
        
        if not original_setting:
            original_setting = DBSetting(key="custom_evaluation_rules", value=legacy_rules)
            db.add(original_setting)
        else:
            original_setting.value = legacy_rules
        db.commit()
        
        # Fetch through get_settings_from_db which should automatically migrate
        settings = get_settings_from_db(db)
        rules = settings["custom_evaluation_rules"]
        
        # It should have migrated to the 5 new default prompts (5 Pilares)
        assert len(rules) == 5
        assert "Pilar 1:" in rules[0]["title"]
        assert "Pilar 2:" in rules[1]["title"]
        
        # Restore backup
        if original_value is not None:
            original_setting.value = original_value
            db.commit()
        else:
            db.delete(original_setting)
            db.commit()
    finally:
        db.close()
