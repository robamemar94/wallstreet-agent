"""Seguimiento de avisos («Requiere tu atención») y señales del monitor de precios.

Cada aviso tiene una clave estable (sin cifras que cambian a diario, como el precio o la ventana de 5 sesiones)
para poder marcarlo como visto y guardar en el histórico las señales que dejan de estar activas.
"""
from typing import Any, Dict, List, Tuple


def signal_key(scope: str, s: Dict[str, Any]) -> str:
    window = "5d" if s.get("since") else "1d"   # movimiento de 5 sesiones o del día / nivel
    return f"{scope}|sig|{s['ticker']}|{s['kind']}|{s.get('tone')}|{window}"


def attention_key(scope: str, a: Dict[str, Any]) -> str:
    return f"{scope}|att|{a.get('type')}|{a.get('ticker')}|{a.get('ref') or ''}"


def track(repo, scope: str, attention: List[Dict[str, Any]], signals: List[Dict[str, Any]],
          history_limit: int = 200) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Sincroniza con la BBDD y devuelve (avisos con key/seen, señales con key/first_seen, histórico de señales)."""
    att = [{**a, "key": attention_key(scope, a)} for a in attention]
    eps = repo.sync(scope, "attention", [{"key": a["key"], "ticker": a.get("ticker"), "kind": a.get("type"),
                                          "text": a["text"]} for a in att])
    att = [{**a, "seen": bool(eps[a["key"]]["seen_at"]), "first_seen": eps[a["key"]]["first_seen"]} for a in att]

    sig = [{**s, "key": signal_key(scope, s)} for s in signals]
    eps = repo.sync(scope, "signal", [{k: s.get(k) for k in ("key", "ticker", "kind", "tone", "text", "date", "since")}
                                      for s in sig])
    sig = [{**s, "first_seen": eps[s["key"]]["first_seen"]} for s in sig]
    return att, sig, repo.history(scope, "signal", history_limit)
