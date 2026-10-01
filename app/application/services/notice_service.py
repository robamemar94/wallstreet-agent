"""Seguimiento de avisos («Requiere tu atención») y señales del monitor de precios.

Cada aviso tiene una clave estable (sin cifras que cambian a diario, como el precio o la ventana de 5 sesiones)
para poder marcarlo como visto y guardar en el histórico las señales que dejan de estar activas.
"""
from typing import Any, Dict, List, Optional, Tuple


def signal_key(scope: str, s: Dict[str, Any]) -> str:
    window = "5d" if s.get("since") else "1d"   # movimiento de 5 sesiones o del día / nivel
    return f"{scope}|sig|{s['ticker']}|{s['kind']}|{s.get('tone')}|{window}"


def attention_key(scope: str, a: Dict[str, Any]) -> str:
    return f"{scope}|att|{a.get('type')}|{a.get('ticker')}|{a.get('ref') or ''}"


def track(repo, scope: str, attention: List[Dict[str, Any]], signals: List[Dict[str, Any]],
          history_limit: int = 200, no_data: Optional[set] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Sincroniza con la BBDD y devuelve (avisos con key/seen, señales con key/first_seen, histórico de señales),
    sin los descartados por el usuario.
    no_data: empresas sin precios en esta carga; sus señales activas se mantienen (no es que hayan terminado)."""
    att = [{**a, "key": attention_key(scope, a)} for a in attention]
    eps = repo.sync(scope, "attention", [{"key": a["key"], "ticker": a.get("ticker") or a.get("label"), "kind": a.get("type"),
                                          "text": a["text"], "href": a.get("href")} for a in att])
    att = [{**a, "seen": bool(eps[a["key"]]["seen_at"]), "first_seen": eps[a["key"]]["first_seen"]} for a in att
           if not eps[a["key"]]["dismissed"]]   # los descartados no se muestran

    sig = [{**s, "key": signal_key(scope, s)} for s in signals]
    eps = repo.sync(scope, "signal", [{k: s.get(k) for k in ("key", "ticker", "kind", "tone", "text", "date", "since")}
                                      for s in sig], keep_tickers=no_data)
    # las mantenidas por falta de datos siguen mostrándose con su último texto
    known = {s["key"] for s in sig}
    sig += [{"ticker": e["ticker"], "kind": e["kind"], "tone": e["tone"], "text": e["text"], "date": e["date"], "since": e["since"],
             "key": k} for k, e in eps.items() if k not in known]
    sig = [{**s, "first_seen": eps[s["key"]]["first_seen"], "seen": bool(eps[s["key"]]["seen_at"])} for s in sig
           if not eps[s["key"]]["dismissed"]]
    return att, sig, repo.history(scope, "signal", history_limit)
