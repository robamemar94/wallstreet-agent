"""Agenda de Mercado (vista «Cartera»): lo que viene (resultados, fechas ex y cobros de dividendos) hoy y mañana o en
los próximos 7 o 30 días y lo que ha pasado hoy (desde ayer) o en los últimos 7 o 30 (variación, valores que más han
movido la cartera, dividendos devengados, operaciones y resultados publicados). Días corridos, no semana o mes
naturales, para que la vista no se quede casi vacía un lunes o un día 1. Solo reúne datos que la app ya tiene."""
import datetime
from typing import Any, Dict, List, Optional

# «hoy»: lo que viene hoy y mañana; lo pasado, desde ayer (los resultados de EE. UU. salen tras su cierre, de noche
# aquí). Su variación es la sesión de hoy («1d» de portfolio_changes, que no es un periodo corrido).
WINDOWS = {"hoy": {"days": 1, "period": "1d"}, "semana": {"days": 7, "period": "7d"}, "mes": {"days": 30, "period": "30d"}}
ROLLING_DAYS = tuple(w["days"] for k, w in WINDOWS.items() if k != "hoy")   # periodos que hay que pedir a portfolio_changes
TOP_MOVERS = 3


def period_start(key: str, today: datetime.date) -> datetime.date:
    """Inicio de lo pasado: ayer, o hace 7 o 30 días."""
    return today - datetime.timedelta(days=WINDOWS[key]["days"])


def _d(v) -> Optional[datetime.date]:
    if not v:
        return None
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])


def upcoming_items(today: datetime.date, days: int, results: List[Dict[str, Any]],
                   dividends: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """results: [{ticker, date, thesis_id, held}] · dividends: pagos de la previsión (dividend_forecast)."""
    end = today + datetime.timedelta(days=days)
    items = []
    for r in results:
        d = _d(r.get("date"))
        if d and today <= d <= end:
            items.append({"date": d, "ticker": r["ticker"], "kind": "results", "held": r.get("held", True),
                          "thesis_id": r.get("thesis_id"), "estimated": False})
    for e in dividends:
        ex, pay = _d(e.get("ex_date")), _d(e.get("pay_date"))
        base = {"ticker": e["ticker"], "estimated": e.get("estimated", False), "held": True, "gross_eur": e.get("gross_eur"),
                "net_eur": e.get("net_eur"), "amount": e.get("amount"), "currency": e.get("currency")}
        if ex and today <= ex <= end:
            items.append({**base, "date": ex, "kind": "ex"})
        if pay and today <= pay <= end:
            items.append({**base, "date": pay, "kind": "pay"})
    order = {"results": 0, "ex": 1, "pay": 2}
    items.sort(key=lambda i: (i["date"], order[i["kind"]], i["ticker"]))
    for i in items:
        i["days"] = (i["date"] - today).days
    return items


def week_done(today: datetime.date, reported: List[Dict[str, Any]],
              collected: List[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """Lo de esta semana que ya ha pasado (del lunes a ayer), para la línea resumida de «Esta semana»: resultados
    presentados (reported: [{ticker, date, surprise_pct, thesis_id}]) y dividendos cobrados (collected: [{ticker, date,
    gross_eur}]), por fecha. El lunes, nada."""
    monday = today - datetime.timedelta(days=today.weekday())
    within = lambda v: _d(v) is not None and monday <= _d(v) < today
    items = [{"date": _d(r["date"]), "ticker": r["ticker"], "kind": "results", "surprise_pct": r.get("surprise_pct"),
              "thesis_id": r.get("thesis_id")} for r in reported if within(r["date"])]
    items += [{"date": _d(c["date"]), "ticker": c["ticker"], "kind": "pay", "gross_eur": c.get("gross_eur")}
              for c in collected if within(c["date"])]
    return sorted(items, key=lambda i: (i["date"], i["kind"] != "results", i["ticker"]))


def group_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Una línea por empresa y sentido (compra / venta): acciones en total, precio medio ponderado, cuántas operaciones
    y entre qué fechas. La más reciente primero."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    for t in transactions:
        g = groups.setdefault((t["ticker"], t["type"]), {"ticker": t["ticker"], "type": t["type"], "currency": t.get("currency"),
                                                         "shares": 0.0, "amount": 0.0, "count": 0, "first": t["date"][:10],
                                                         "last": t["date"][:10]})
        g["shares"] += t["shares"]
        g["amount"] += t["shares"] * (t.get("price") or 0)
        g["count"] += 1
        g["first"], g["last"] = min(g["first"], t["date"][:10]), max(g["last"], t["date"][:10])
    out = list(groups.values())
    for g in out:
        g["avg_price"] = g["amount"] / g["shares"] if g["shares"] and g["amount"] else None
    return sorted(out, key=lambda g: (g["last"], g["ticker"]), reverse=True)


def past_summary(start: datetime.date, today: datetime.date, period: Optional[Dict[str, Any]],
                 pnl_by_ticker: Dict[str, Optional[float]], collected: List[Dict[str, Any]],
                 transactions: List[Dict[str, Any]], reported: List[Dict[str, Any]]) -> Dict[str, Any]:
    """pnl_by_ticker: ganancia en € de cada posición en el periodo (sin contar compras ni ventas) ·
    collected: [{ticker, date, gross_eur}] · transactions: [{ticker, date, type, shares, price, currency}] ·
    reported: [{ticker, date, surprise_pct}]."""
    within = lambda v: _d(v) is not None and start <= _d(v) <= today
    moves = sorted(((t, v) for t, v in pnl_by_ticker.items() if v), key=lambda x: x[1])
    divs = sorted((c for c in collected if within(c["date"])), key=lambda c: c["date"], reverse=True)
    return {
        "start": start,
        "pct": (period or {}).get("pct"), "abs_eur": (period or {}).get("abs_eur"),
        "benchmark_pct": (period or {}).get("benchmark_pct"), "incomplete": (period or {}).get("incomplete") or [],
        "gainers": [{"ticker": t, "pnl_eur": v} for t, v in reversed(moves) if v > 0][:TOP_MOVERS],
        "losers": [{"ticker": t, "pnl_eur": v} for t, v in moves if v < 0][:TOP_MOVERS],
        "dividends": divs, "dividends_eur": sum(c["gross_eur"] for c in divs),
        "transactions": group_transactions([t for t in transactions if within(t["date"])]),
        "reported": sorted((r for r in reported if within(r["date"])), key=lambda r: r["date"], reverse=True),
    }


def build_agenda(today: datetime.date, results: List[Dict[str, Any]], dividends: List[Dict[str, Any]],
                 periods: Dict[str, Dict[str, Any]], positions: Dict[str, Dict[str, Any]],
                 collected: List[Dict[str, Any]], transactions: List[Dict[str, Any]],
                 reported: List[Dict[str, Any]], symbol=None) -> Dict[str, Dict[str, Any]]:
    """Las tres vistas de la agenda: «hoy» (hoy y mañana; lo pasado, desde ayer), «semana» (7 días adelante y atrás)
    y «mes» (30 días adelante y atrás).
    periods y positions: los de price_monitor_service.portfolio_changes con rolling_days=ROLLING_DAYS (el «1d»,
    mejor con los precios en directo).
    symbol(currency, ticker): símbolo de la moneda para el precio medio de las operaciones."""
    out = {}
    for key, w in WINDOWS.items():
        pnl = {t: (p.get("pnl_eur") or {}).get(w["period"]) for t, p in positions.items()}
        out[key] = {
            "days": w["days"],
            "upcoming": upcoming_items(today, w["days"], results, []),
            # dividendos a cobrar: solo los anunciados (fecha ex o de cobro dentro de la ventana), no los estimados
            "dividends": upcoming_items(today, w["days"], [], [e for e in dividends if not e.get("estimated")]),
            "past": past_summary(period_start(key, today), today, periods.get(w["period"]), pnl, collected, transactions, reported),
        }
        for g in out[key]["past"]["transactions"]:
            g["cur"] = symbol(g["currency"], g["ticker"]) if symbol else (g["currency"] or "")
    return out


# --- Agenda del resto: empresas que sigues sin tenerlas en cartera ---

def build_other_agenda(today: datetime.date, results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Vista «Resto» de la agenda, con las mismas ventanas que la de la cartera: quién presenta resultados en los
    próximos 7 / 30 días. results: [{ticker, date, thesis_id}]."""
    return {key: {"days": w["days"], "upcoming": upcoming_items(today, w["days"], [{**r, "held": False} for r in results], [])}
            for key, w in WINDOWS.items()}


# --- Watchlist del día ---

WATCH_NEAR_PCT = 5      # «a punto»: le falta caer como mucho esto para llegar a su precio de compra
WATCH_MOVE_PCT = 3      # movimiento fuerte del día


def watchlist_today(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Lo que hay que mirar hoy de la watchlist: en zona de compra, a punto de entrar y las que más se mueven.
    items: [{ticker, thesis_id, drop, day_pct, closed}] (drop: lo que tiene que caer hasta su precio de compra, en %;
    ≤0 = ya en zona; None = sin valoración)."""
    zone = sorted((i for i in items if i.get("drop") is not None and i["drop"] <= 0), key=lambda i: i["drop"])
    near = sorted((i for i in items if i.get("drop") is not None and 0 < i["drop"] <= WATCH_NEAR_PCT), key=lambda i: i["drop"])
    movers = sorted((i for i in items if not i.get("closed") and i.get("day_pct") is not None and abs(i["day_pct"]) >= WATCH_MOVE_PCT),
                    key=lambda i: -abs(i["day_pct"]))
    return {"zone": zone, "near": near, "movers": movers}


# --- Titular del Centro de mando y «desde tu última visita» ---

def since_last_visit(since: Optional[str], notices: List[Dict[str, Any]], news: List[Dict[str, Any]],
                     reported: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Qué ha aparecido desde la última visita (since: fecha y hora local ISO, la guarda el navegador).
    notices: avisos y señales con first_seen · news: noticias con found_at · reported: [{ticker, date}]."""
    if not since:
        return None
    since = _iso(since)
    return {
        "since": since,
        "notices": [n["key"] for n in notices if _iso(n.get("first_seen")) > since],
        "news": [n["id"] for n in news if _iso(n.get("found_at")) > since],
        "reported": sorted({r["ticker"] for r in reported if (r.get("date") or "")[:10] >= since[:10]}),
    }


def _iso(v) -> str:
    """Fecha y hora como texto comparable (datetime o texto con espacio o «T»)."""
    if not v:
        return ""
    return (v.isoformat() if hasattr(v, "isoformat") else str(v)).replace(" ", "T")[:19]


# --- Resumen del Centro de mando para la tarjeta «Mi Cartera» de Mercado ---

PULSE_RESULTS_DAYS = 30
NOTICE_PRIORITY = {"avisos": 0, "noticias": 1, "alertas": 2, "monitor": 3}


def pulse(today: datetime.date, notifications: List[Dict[str, Any]], results: List[Dict[str, Any]],
          dividends: List[Dict[str, Any]], period: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Cuatro líneas: avisos sin leer de la cartera (y el más importante), próximos resultados, próximo cobro de dividendo
    (confirmado si lo hay) y la variación de los últimos 30 días frente al índice.
    notifications: las de notification_service.collect · results: [{ticker, date}] de la cartera ·
    dividends: pagos de la previsión · period: el «30d» de portfolio_changes."""
    unread = [n for n in notifications if not n["read"] and n.get("portfolio") and n.get("counts", True)]
    top = min(unread, key=lambda n: (NOTICE_PRIORITY.get(n["group"], 9), -_ts(n.get("date"))), default=None)
    end = today + datetime.timedelta(days=PULSE_RESULTS_DAYS)
    upcoming = sorted((r for r in results if _d(r.get("date")) and today <= _d(r["date"]) <= end), key=lambda r: r["date"])
    first = _d(upcoming[0]["date"]) if upcoming else None
    future = [e for e in dividends if _d(e.get("date")) and _d(e["date"]) >= today]
    confirmed = [e for e in future if not e.get("estimated")]
    nxt = min(confirmed or future, key=lambda e: _d(e["date"]), default=None)
    return {
        "unread": len(unread),
        "top_notice": {"ticker": top["ticker"], "kind": top["kind"], "text": top["text"], "href": top["href"]} if top else None,
        "results": {"first_date": first.isoformat(), "days": (first - today).days,
                    "first_tickers": [r["ticker"] for r in upcoming if _d(r["date"]) == first],
                    "count": len(upcoming)} if upcoming else None,
        "dividend": {"ticker": nxt["ticker"], "date": _d(nxt["date"]).isoformat(), "gross_eur": nxt["gross_eur"],
                     "estimated": bool(nxt.get("estimated")), "kind": "pago" if nxt.get("pay_date") else "ex"} if nxt else None,
        "period": {"days": PULSE_RESULTS_DAYS, "pct": period.get("pct"), "abs_eur": period.get("abs_eur"),
                   "benchmark_pct": period.get("benchmark_pct"), "incomplete": period.get("incomplete") or []} if period and period.get("pct") is not None else None,
    }


def _ts(v) -> float:
    try:
        return datetime.datetime.fromisoformat(str(v)[:19]).timestamp()
    except Exception:
        return 0.0


# --- Aviso de próximos resultados («Requiere tu atención») ---

RESULTS_NOTICE_DAYS = 7


def upcoming_results_notices(results: List[Dict[str, Any]], held: set, today: datetime.date) -> List[Dict[str, Any]]:
    """Un aviso por empresa en cartera que presenta resultados en los próximos 7 días (se va solo al publicarlos;
    entonces salta el de «resultados publicados sin revisar»). results: [{ticker, date, thesis_id}].
    La fecha va en la clave (ref): si la empresa la cambia, es un aviso nuevo."""
    out = []
    for r in results:
        d = _d(r.get("date"))
        if r["ticker"] not in held or not d or not 0 <= (d - today).days <= RESULTS_NOTICE_DAYS:
            continue
        days = (d - today).days
        when = "hoy" if days == 0 else "mañana" if days == 1 else f"en {days} d"
        out.append({"prio": 2, "ticker": r["ticker"], "type": "upcoming_results", "ref": d.isoformat(),
                    "text": f"Presenta resultados el {d.strftime('%d/%m')} ({when})",
                    "href": f"/tesis/{r['thesis_id']}" if r.get("thesis_id") else f"/ticker/{r['ticker']}"})
    return out
