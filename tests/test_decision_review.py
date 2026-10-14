import pandas as pd
import pytest

from app.application.services import decision_review_service as dr


def _df(days=400):
    idx = pd.bdate_range("2025-01-01", periods=days)
    stock = pd.Series(100.0, index=idx)
    stock.iloc[100:] = 120.0                      # sube un 20 % justo después del día 99
    return pd.DataFrame({"AAA": stock, "SPY": 400.0, "USDEUR=X": 0.9}, index=idx)


def test_buy_compared_with_the_index_in_eur():
    df = _df()
    day = df.index[99].date().isoformat()
    out = dr.build(df, [{"ticker": "AAA", "date": day, "type": "BUY", "shares": 10, "price": 100.0, "currency": "USD", "fx": None}], {})
    op = out["ops"][0]
    r = op["review"]
    assert r["since"] == pytest.approx(20) and r["excess"] == pytest.approx(20)
    assert r["horizons"]["1m"] == pytest.approx(20) and "12m" in r["horizons"]
    assert r["entry"] == "lateral" and op["amount_eur"] == pytest.approx(900)   # cambio del día si la operación no lo trae
    assert out["summary"]["buys"] == {"n": 1, "beat": 1, "avg": pytest.approx(20)} and out["benchmark"] == "S&P 500"


def test_orders_of_the_same_day_are_one_decision():
    df = _df()
    day = df.index[99].date().isoformat()
    txs = [{"ticker": "AAA", "date": day, "type": "BUY", "shares": 10, "price": 100.0, "currency": "USD", "fx": 0.9},
           {"ticker": "AAA", "date": day, "type": "BUY", "shares": 30, "price": 80.0, "currency": "USD", "fx": 0.9},
           {"ticker": "AAA", "date": df.index[150].date().isoformat(), "type": "BUY", "shares": 1, "price": 120.0, "currency": "USD", "fx": 0.9}]
    data = {"AAA": {"tx_notes": {dr.tx_ref(day, "BUY", 30, 80.0): "Segunda orden"}}}
    ops = dr.build(df, txs, data)["ops"]
    assert len(ops) == 2
    same_day = ops[1]
    assert same_day["orders"] == 2 and same_day["shares"] == 40 and same_day["price"] == pytest.approx(85)
    assert same_day["amount_eur"] == pytest.approx(3060) and same_day["note"] == "Segunda orden"
    assert same_day["refs"] == [dr.tx_ref(day, "BUY", 10, 100.0), dr.tx_ref(day, "BUY", 30, 80.0)]


def test_operation_older_than_history_is_not_measured_from_the_first_price():
    df = _df()
    out = dr.build(df, [{"ticker": "AAA", "date": "2022-03-16", "type": "BUY", "shares": 1, "price": 1.0, "currency": "USD"}], {})
    assert out["ops"][0]["review"] is None and out["missing"] == ["AAA"]


def test_sell_after_which_the_stock_fell_and_entry_after_a_drop():
    df = _df()
    df.loc[df.index[200]:, "AAA"] = 90.0          # cae un 25 % (de 120 a 90)
    sell_day, buy_day = df.index[99].date().isoformat(), df.index[220].date().isoformat()
    txs = [{"ticker": "AAA", "date": sell_day, "type": "SELL", "shares": 1, "price": 100.0, "currency": "USD", "fx": 0.9},
           {"ticker": "AAA", "date": buy_day, "type": "BUY", "shares": 1, "price": 90.0, "currency": "USD", "fx": 0.9}]
    data = {"AAA": {"tx_notes": {dr.tx_ref(buy_day, "BUY", 1, 90.0): "Cae sin motivo"}}}
    out = dr.build(df, txs, data)
    buy, sell = out["ops"]
    assert buy["note"] == "Cae sin motivo" and buy["review"]["entry"] == "caida"
    assert out["summary"]["sells"]["rose"] == 0 and out["summary"]["sells"]["vs"]["beat"] == 0
    assert out["summary"]["with_reason"] == 1
    assert out["summary"]["entry"] == [{"key": "caida", "label": "Tras caída", "n": 1, "beat": 0, "avg": 0.0}]


def test_zone_summary_only_with_snapshots():
    df = _df()
    day = df.index[99].date().isoformat()
    tx = {"ticker": "AAA", "date": day, "type": "BUY", "shares": 1, "price": 100.0, "currency": "USD", "fx": 0.9}
    assert dr.build(df, [tx], {})["summary"]["zones"] == []
    data = {"AAA": {"tx_snapshots": {dr.tx_ref(day, "BUY", 1, 100.0): {"zone": "zona", "drop": -3.0}}}}
    zones = dr.build(df, [tx], data)["summary"]["zones"]
    assert zones[0]["label"] == "En zona de compra" and zones[0]["beat"] == 1


def test_note_saved_on_every_order_of_the_decision_and_cleared():
    from fastapi.testclient import TestClient
    from main import app
    from app.infrastructure.db.database import SessionLocal
    from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
    from app.infrastructure.db.models import DBAsset
    ticker = (SessionLocal().query(DBAsset.ticker).first() or [None])[0]
    if not ticker:
        pytest.skip("la copia de la base de datos no tiene activos")
    refs = ["2020-01-02|BUY|1|10", "2020-01-02|BUY|2|11"]
    client = TestClient(app)
    assert client.post("/api/portfolio/transaction/note", json={"ticker": ticker, "refs": refs, "note": " Barata "}).json()["note"] == "Barata"
    notes = SqlAlchemyAssetRepository(SessionLocal()).get_asset_data(ticker)["tx_notes"]
    assert notes[refs[0]] == notes[refs[1]] == "Barata"
    client.post("/api/portfolio/transaction/note", json={"ticker": ticker, "refs": refs, "note": ""})
    notes = SqlAlchemyAssetRepository(SessionLocal()).get_asset_data(ticker).get("tx_notes") or {}
    assert not set(refs) & set(notes)


def test_staged_entry_ends_after_a_gap_or_a_sale():
    df = _df()
    df.loc[df.index[110]:, "AAA"] = 60.0          # tras subir a 120, cae a 60: el segundo tramo sale a mitad de precio
    d = lambda i: df.index[i].date().isoformat()
    buy = lambda i, price: {"ticker": "AAA", "date": d(i), "type": "BUY", "shares": 1, "price": price, "currency": "USD", "fx": 0.9}
    txs = [buy(100, 120.0), buy(115, 60.0),                                  # misma entrada: 15 sesiones entre medias
           {"ticker": "AAA", "date": d(130), "type": "SELL", "shares": 1, "price": 60.0, "currency": "USD", "fx": 0.9},
           buy(135, 60.0),                                                   # tras una venta: entrada nueva
           buy(250, 60.0)]                                                   # meses después: otra entrada
    out = dr.build(df, txs, {})
    entries = sorted(out["entries"], key=lambda c: c["start"])
    assert [c["tranches"] for c in entries] == [2, 1, 1]
    first = entries[0]
    # 108 € a 108 y 54 € a 54 → precio medio 81 (162 € por 2 acciones) frente a 108 el primer día: un 25 % más barato
    assert first["saving"] == pytest.approx(25) and first["since"] == pytest.approx(-100 / 3)
    assert first["excess"] == pytest.approx(-100 / 3) and not first["ongoing"]
    assert out["summary"]["tranches"] == {"n": 1, "saved": 1, "avg": pytest.approx(25)}


def test_measured_from_your_price_adjusted_for_splits():
    """Como BKNG: compras por debajo del cierre y después hay un split 25×1 (los precios de yfinance vienen ajustados)."""
    df = _df()
    df["AAA"] = 100.0                              # ajustado por el split: 2.500 $ de entonces = 100 $ de hoy
    day = df.index[99].date().isoformat()
    tx = {"ticker": "AAA", "date": day, "type": "BUY", "shares": 1, "price": 2375.0, "currency": "USD", "fx": 0.9}
    late_split = {"AAA": [(df.index[150].date().isoformat(), 25.0)]}
    r = dr.build(df, [tx], {}, splits=late_split)["ops"][0]["review"]
    assert r["from_price"] and r["since"] == pytest.approx(100 * (100 / 95 - 1))   # compraste a 95 $ de hoy, un 5 % bajo el cierre
    # sin el split anotado, tu precio (2.375 $) no cuadra con el cierre (100 $): se mide desde el cierre
    r = dr.build(df, [tx], {})["ops"][0]["review"]
    assert not r["from_price"] and r["since"] == pytest.approx(0)
