import datetime

import pandas as pd

from app.interfaces.views import market_views as m


def _bars(ts_utc, price):
    return pd.DataFrame({"MSFT": [price]}, index=pd.DatetimeIndex([pd.Timestamp(ts_utc)]))


def test_post_market_of_last_session():
    data = {"MSFT": {"price": 100.0, "last_date": datetime.date(2026, 10, 15)}}
    q = m.extended_from_bars(_bars("2026-10-15 23:55+00:00", 99.0), data)["MSFT"]   # 19:55 en Nueva York
    assert q["kind"] == "post" and q["pct"] == -1.0 and q["time"] == "01:55"


def test_pre_market_of_today_and_regular_bar_ignored():
    data = {"MSFT": {"price": 100.0, "last_date": datetime.date(2026, 10, 15)}}
    assert m.extended_from_bars(_bars("2026-10-16 12:00+00:00", 102.0), data)["MSFT"]["kind"] == "pre"   # 8:00 NY
    assert m.extended_from_bars(_bars("2026-10-15 19:00+00:00", 102.0), data) == {}   # 15:00 NY: sesión normal
    assert m.extended_from_bars(_bars("2026-10-15 12:00+00:00", 102.0), data) == {}   # pre-market de la sesión ya hecha


def test_ext_quote_only_when_not_open():
    m.INDEX_CACHE["data"]["ZZZT"] = {"price": 1.0, "last_date": datetime.date(2026, 10, 15), "ext": {"pct": 1.0}}
    try:
        assert m.ext_quote("ZZZT", datetime.date(2026, 10, 16)) == {"pct": 1.0}
        assert m.ext_quote("ZZZT", datetime.date(2026, 10, 15)) is None   # ya ha abierto hoy
    finally:
        m.INDEX_CACHE["data"].pop("ZZZT")
    assert not m.is_us_listing("SAP.DE") and m.is_us_listing("MSFT")


def test_ext_tag_icon_only_unless_big_move():
    from app.infrastructure.templates import templates
    t = templates.env.from_string('{% from "_ui_macros.html" import ext_tag %}{{ ext_tag(e) }}')
    small = t.render(e={"pct": -0.52, "price": 536.65, "kind": "post", "time": "01:55"})
    big = t.render(e={"pct": -6.3, "price": 500.0, "kind": "post", "time": "01:55"})
    assert "bi-moon-stars-fill" in small and "ext-big" not in small and "Post-market: 536,65 (-0,52 %) · 01:55" in small and "%</span>" not in small
    assert "ext-big" in big and "-6,3%</span>" in big
    assert t.render(e={"pct": 0.0, "price": 1, "kind": "pre", "time": "10:00"}).strip() == ""


def test_stale_pill_puts_ext_icon_first():
    """En las tablas el icono va a la izquierda de la píldora, para que las píldoras queden alineadas."""
    from app.infrastructure.templates import templates
    t = templates.env.from_string('{% from "_ui_macros.html" import stale_pill %}{{ stale_pill(1.2, "2026-10-15", ext=e) }}')
    html = t.render(e={"pct": -0.52, "price": 536.65, "kind": "post", "time": "01:55"})
    assert html.index("ext-tag") < html.index("chg-pill")


def test_ext_day_pre_and_post_until_asia_opens():
    """Var. día € de la cartera con el horario extendido: pre-market antes de abrir; post-market de la sesión hasta que
    abre Australia (10:00 en Sídney)."""
    import datetime
    from zoneinfo import ZoneInfo
    from app.interfaces.views import market_views as mv
    mad = ZoneInfo("Europe/Madrid")
    thu, fri = datetime.date(2026, 10, 15), datetime.date(2026, 10, 16)
    post = {"price": 101.0, "chg": 1.0, "pct": 1.0, "kind": "post", "time": "23:00", "date": fri.isoformat()}
    mv.INDEX_CACHE["data"]["ZZZT"] = {"price": 100.0, "last_date": fri, "ext": post}
    try:
        assert mv.ext_day("ZZZT", datetime.datetime(2026, 10, 16, 23, 0, tzinfo=mad))["today"]
        # sábado 00:30 en Madrid (9:30 en Sídney): aún cuenta; a la 01:30 ya no
        assert mv.ext_day("ZZZT", datetime.datetime(2026, 10, 17, 0, 30, tzinfo=mad))
        assert mv.asia_open_after(fri).weekday() == 0   # tras el viernes, Australia abre el lunes
        # el post-market guardado de otra sesión no corrige la de hoy
        mv.INDEX_CACHE["data"]["ZZZT"] = {"price": 100.0, "last_date": fri, "ext": {**post, "date": thu.isoformat()}}
        assert mv.ext_day("ZZZT", datetime.datetime(2026, 10, 16, 23, 0, tzinfo=mad)) is None
        # pre-market: solo si su mercado aún no ha abierto hoy
        pre = {**post, "kind": "pre", "date": fri.isoformat()}
        mv.INDEX_CACHE["data"]["ZZZT"] = {"price": 100.0, "last_date": thu, "ext": pre}
        assert mv.ext_day("ZZZT", datetime.datetime(2026, 10, 16, 11, 0, tzinfo=mad))["kind"] == "pre"
        mv.INDEX_CACHE["data"]["ZZZT"] = {"price": 100.0, "last_date": fri, "ext": pre}
        assert mv.ext_day("ZZZT", datetime.datetime(2026, 10, 16, 16, 0, tzinfo=mad)) is None
    finally:
        mv.INDEX_CACHE["data"].pop("ZZZT", None)


def test_ext_day_eur_corrects_session():
    from app.interfaces.views.portfolio_views import ext_day_eur
    post = {"price": 101.0, "chg": 1.0, "pct": 1.0, "kind": "post", "time": "23:00", "today": True}
    x = ext_day_eur(10, post, {"closed": False, "eur": 50.0, "last_abs": None}, 0.9)
    assert x["eur"] == 9.0 and x["session"] == 50.0 and x["total"] == 59.0
    x = ext_day_eur(10, {**post, "kind": "pre"}, {"closed": True, "eur": 0.0, "last_abs": 2.0}, 0.9)
    assert x["session"] is None and x["total"] == 9.0
