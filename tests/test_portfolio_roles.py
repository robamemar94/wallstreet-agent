"""Roles de cartera y Portfolio ideal por rol."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.application.services import portfolio_roles_service as roles_service
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service
from main import app

ROLES = [
    {"key": "CORE", "name": "Core", "min": 30, "max": 40},
    {"key": "OPTIONALITY", "name": "Optionality", "min": 5, "max": 10},
]


def c(ticker, role=None, value=0.0, drop=None):
    return {"ticker": ticker, "role": role, "held": value > 0, "value_eur": value, "drop_pct": drop}


def test_config_has_the_four_roles_with_ranges():
    roles = roles_service.load_roles()
    assert [r["key"] for r in roles] == ["CORE", "GROWTH", "QUALITY", "OPTIONALITY"]
    assert [(r["min"], r["max"]) for r in roles] == [(30, 40), (25, 35), (20, 30), (5, 10)]


def test_eligible_only_tracked_favorite_or_held():
    assert roles_service.eligible({"status": "ACCEPTED"}, False, False)
    assert roles_service.eligible({}, True, False)
    assert roles_service.eligible({"status": "REJECTED"}, False, True)
    assert not roles_service.eligible({"status": "STANDBY"}, False, False)
    assert not roles_service.eligible({}, False, False)


def test_build_weights_gaps_and_unassigned():
    companies = [c("MSFT", "CORE", 2000), c("DSGX", "CORE", 500), c("HLMA.L", "CORE", drop=12.0),
                 c("LIFCO", "CORE", drop=-3.0), c("EVO", "OPTIONALITY", 1500), c("BKNG", None, 6000), c("TEQ", None)]
    out = roles_service.build(ROLES, companies, 10000)
    core, opt = out["groups"]

    assert [x["ticker"] for x in core["held"]] == ["MSFT", "DSGX"]          # por peso
    assert [x["ticker"] for x in core["candidates"]] == ["LIFCO", "HLMA.L"]  # la más cerca del precio de compra primero
    assert core["weight"] == 25.0 and core["band"] == "bajo" and core["gap_pct"] == 5.0 and core["gap_eur"] == 500
    assert opt["weight"] == 15.0 and opt["band"] == "alto" and opt["gap_pct"] == -5.0
    assert core["held"][0]["weight"] == 20.0 and core["candidates"][0]["weight"] is None

    assert [x["ticker"] for x in out["unassigned_held"]] == ["BKNG"]
    assert [x["ticker"] for x in out["unassigned_watch"]] == ["TEQ"]
    assert out["assigned_weight"] == 40.0
    assert (out["target_min"], out["target_max"]) == (35, 50)


def test_build_with_empty_portfolio():
    out = roles_service.build(ROLES, [c("MSFT", "CORE")], 0)
    assert out["groups"][0]["weight"] == 0.0 and out["groups"][0]["gap_eur"] == 0.0


class FakeAssetRepo:
    def __init__(self, data, favorites=()):
        self.data, self.favorites = data, set(favorites)

    def get_asset_data(self, ticker):
        return self.data.setdefault(ticker, {})

    def save_asset_data(self, ticker, key, value):
        self.data.setdefault(ticker, {})[key] = value

    def is_favorite(self, ticker):
        return ticker in self.favorites


def client_with(repo, held=()):
    portfolio = SimpleNamespace(get_active_portfolio=lambda: [SimpleNamespace(ticker=t, shares=1.0) for t in held])
    app.dependency_overrides[get_asset_repository] = lambda: repo
    app.dependency_overrides[get_portfolio_service] = lambda: portfolio
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_role_endpoint_sets_and_clears():
    repo = FakeAssetRepo({"MSFT": {"status": "ACCEPTED"}})
    client = client_with(repo)
    assert client.post("/api/role/MSFT", json={"role": "CORE"}).status_code == 200
    assert repo.data["MSFT"]["portfolio_role"] == "CORE"
    assert client.post("/api/role/MSFT", json={"role": ""}).status_code == 200
    assert repo.data["MSFT"]["portfolio_role"] is None


def test_role_endpoint_rejects_unknown_role_and_untracked_company():
    repo = FakeAssetRepo({"TEQ.ST": {"status": "STANDBY"}, "EVO.ST": {"status": "REJECTED"}})
    client = client_with(repo, held=["EVO.ST"])
    assert client.post("/api/role/TEQ.ST", json={"role": "BOGUS"}).status_code == 400
    assert client.post("/api/role/TEQ.ST", json={"role": "OPTIONALITY"}).status_code == 400
    assert "portfolio_role" not in repo.data["TEQ.ST"]
    assert client.post("/api/role/EVO.ST", json={"role": "OPTIONALITY"}).status_code == 200   # en cartera


def test_rejecting_a_company_removes_its_role():
    repo = FakeAssetRepo({"ROKO": {"status": "ACCEPTED", "portfolio_role": "OPTIONALITY"}})
    client = client_with(repo)
    assert client.post("/api/status/ROKO", json={"status": "REJECTED"}).json()["status"] == "success"
    assert repo.data["ROKO"]["portfolio_role"] is None
