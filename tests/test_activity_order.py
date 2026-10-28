from app.interfaces.views.market_views import activity_key, activity_move, ext_move


def mv(name, pct, not_open=False, ext_pct=None, kind="pre"):
    return {"name": name, "pct": pct, "not_open": not_open, "ext": {"kind": kind, "pct": ext_pct} if ext_pct is not None else None}


def test_cuenta_el_pre_o_post_market_grande_de_lo_que_aun_no_ha_abierto():
    assert ext_move(mv("A", 1, True, 1.0)) is None             # pequeño
    assert ext_move(mv("A", 1, True, 3.0, kind="post")) == 3.0  # post-market de su última sesión, de madrugada
    assert ext_move(mv("A", 1, False, 3.0)) is None             # ya ha abierto hoy: no cambia de lista ni de orden
    assert ext_move(mv("A", 1, True, -1.5)) == -1.5


def test_primero_lo_abierto_luego_pre_market_grande_y_al_final_el_resto():
    rows = [mv("viejo", 9.0, True), mv("pre", 0.1, True, 2.0), mv("hoy", 0.5)]
    assert [m["name"] for m in sorted(rows, key=activity_key)] == ["hoy", "pre", "viejo"]


def test_pre_market_se_ordena_por_su_movimiento_y_no_por_ayer():
    rows = [mv("a", 5.0, True, 1.6), mv("b", -2.0, True, 4.0)]
    assert [m["name"] for m in sorted(rows, key=activity_key)] == ["b", "a"]


def test_va_a_ganadores_o_perdedores_segun_el_signo_del_pre_market():
    assert activity_move(mv("a", 0.3, True, -3.0)) < 0
    assert activity_move(mv("b", -0.3, True, 2.0)) > 0
    assert activity_move(mv("c", -0.3, True, 0.5)) == -0.3   # pre-market pequeño: manda la última sesión


def test_post_market_de_hoy_solo_si_es_de_la_sesion_de_hoy():
    import datetime
    from app.interfaces.views import market_views as mvw
    today = datetime.date(2026, 10, 21)
    mvw.INDEX_CACHE["data"]["ZZTEST"] = {"ext": {"kind": "post", "pct": -8.0, "date": "2026-10-21"}}
    try:
        assert mvw.post_today("ZZTEST", today)["pct"] == -8.0
        mvw.INDEX_CACHE["data"]["ZZTEST"]["ext"]["date"] = "2026-10-20"   # el de ayer no corrige la sesión de hoy
        assert mvw.post_today("ZZTEST", today) is None
        mvw.INDEX_CACHE["data"]["ZZTEST"]["ext"] = {"kind": "pre", "pct": 3.0, "date": "2026-10-21"}
        assert mvw.post_today("ZZTEST", today) is None
    finally:
        mvw.INDEX_CACHE["data"].pop("ZZTEST", None)
