from app.interfaces.views.portada_views import bandeja_compact, bandeja_subs


def _i(ticker, text, typ=None, kind="", filt="precio", key="", portfolio=False):
    return {"ticker": ticker, "text": text, "type": typ, "kind": kind, "filter": filt, "key": key, "portfolio": portfolio}


def test_compact_price_moves_and_zones():
    d = bandeja_compact(_i("MELI", "-10,4% en el día, 4,0σ para este valor", "anormal", key="x|1d"))
    assert (d["sub"], d["metric"], d["strong"], d["stale"]) == ("dia", "−10,4 %", True, False)
    f = bandeja_compact(_i("CW", "-7,4% en 5 sesiones, 1,5σ para este valor · ya no se cumple", "anormal", key="x|5d"))
    assert (f["sub"], f["strong"], f["stale"]) == ("5d", False, True)
    k = bandeja_compact(_i("KARNEL-B", "Cae un 21,0% desde el máximo de 52 semanas", kind="caída"))
    assert (k["sub"], k["metric"]) == ("lejos", "−21,0 %")
    z = bandeja_compact(_i("MSFT", "Pasa de justo a barata · CAGR estimado 13,5%", kind="valoración", filt="comprar"))
    assert (z["sub"], z["label"], z["metric"]) == ("to_barata", "Pasa a barata", "13,5 %")


def test_subs_order_down_then_up_by_size_and_stale_last():
    items = [_i("AAA", "-9,0% en 5 sesiones, 1,1σ", "anormal", key="|5d"),
             _i("UUU", "+7,1% en 5 sesiones, 1,1σ", "anormal", key="|5d"),
             _i("GGG", "-7,0% en 5 sesiones, 1,6σ", "anormal", key="|5d", portfolio=True),
             _i("CCC", "-12,0% en 5 sesiones, 1,1σ · ya no se cumple", "anormal", key="|5d")]
    row = bandeja_subs(items)["precio"][0]
    assert [c["ticker"] for c in row["chips"]] == ["AAA", "GGG", "CCC", "UUU"]   # ▼ de más a menos, luego ▲
    assert [c["bj"]["dir"] for c in row["chips"]] == ["down", "down", "down", "up"]
