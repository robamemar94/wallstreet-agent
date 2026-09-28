"""
Localiza y descarga los documentos de unos resultados: comunicado oficial y transcripción completa de la call.

La calidad del análisis de la call depende de leer la transcripción ENTERA (no fragmentos de búsqueda), así que:
1. Gemini con Google Search propone URLs (y deja en el grounding las páginas que consultó de verdad).
2. Se resuelven las redirecciones del grounding y se descargan los candidatos.
3. Se clasifica cada texto (transcripción / comunicado) por su contenido, no por lo que diga el modelo.
"""
import logging
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_CANDIDATES = 10
MAX_DOC_CHARS = 200_000          # ~50k tokens: de sobra para una transcripción; evita páginas desmesuradas
TRANSCRIPT_MIN_WORDS = 3000


def resolve_url(url: str, timeout: int = 15) -> Optional[str]:
    """Las fuentes del grounding son redirecciones de vertexaisearch: devuelve la URL final."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=False)
        return r.headers.get("Location") or None
    except requests.RequestException:
        return None


SLOW_HOSTS = ("globenewswire.com", "businesswire.com", "prnewswire.com")


def fetch_text(url: str, timeout: int = 25) -> Optional[str]:
    from bs4 import BeautifulSoup

    if any(h in url for h in SLOW_HOSTS):
        timeout = 60
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html"):
            return None
    except requests.RequestException as e:
        logger.info("No se pudo descargar %s: %s", url, e)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    body = soup.select_one(".article-body") or soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    return text[:MAX_DOC_CHARS]


def classify_document(text: str, company_terms: List[str]) -> Optional[str]:
    """'transcript', 'press_release' o None, según el contenido."""
    if not text:
        return None
    low = text.lower()
    if not any(t.lower() in low for t in company_terms):
        return None
    words = len(text.split())
    call_markers = sum(m in low for m in ("operator", "question-and-answer", "questions and answers", "analyst",
                                          "prepared remarks", "call participants", "conference call"))
    if words >= TRANSCRIPT_MIN_WORDS and call_markers >= 2:
        return "transcript"
    release_markers = sum(m in low for m in ("revenue", "quarter", "net income", "financial results", "fiscal"))
    if words >= 400 and release_markers >= 3:
        return "press_release"
    return None


def pick_documents(candidates: List[Dict[str, Any]], company_terms: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """De los candidatos descargados ({url, text}) elige la mejor transcripción (la más larga) y el mejor comunicado."""
    best: Dict[str, Optional[Dict[str, Any]]] = {"transcript": None, "press_release": None}
    for c in candidates:
        kind = classify_document(c.get("text") or "", company_terms)
        if not kind:
            continue
        cur = best[kind]
        if kind == "transcript":
            if cur is None or len(c["text"]) > len(cur["text"]):
                best[kind] = c
        elif cur is None or _is_primary(c["url"]) and not _is_primary(cur["url"]):
            best[kind] = c
    return best


def _is_primary(url: str) -> bool:
    return any(d in url for d in ("globenewswire", "businesswire", "prnewswire", "sec.gov", "investor", "/news"))


def download_candidates(urls: List[str]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for raw in urls:
        url = resolve_url(raw)
        if not url or url in seen or url.lower().endswith(".pdf"):
            continue
        seen.add(url)
        text = fetch_text(url)
        if text:
            out.append({"url": url, "text": text})
        if len(out) >= MAX_CANDIDATES:
            break
    return out
