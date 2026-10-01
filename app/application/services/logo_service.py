"""Logos de empresa para las tarjetas de tesis.

Se descargan UNA sola vez por empresa y se guardan en logos_cache/ (nunca se vuelven a pedir): primero el logo
por ticker de FinancialModelingPrep y, si no vale, el icono de la web de la empresa. Si no hay ninguno se anota
para no reintentarlo durante un mes. El usuario puede subir el suyo, que siempre manda.
"""
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

LOGOS_DIR = "logos_cache"
FMP_MIN_BYTES = 800       # FMP devuelve una imagen vacía (~400 B) si no tiene el logo
ICON_MIN_BYTES = 300      # iconos de la web: pequeños pero válidos
RETRY_MISSING_DAYS = 30
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _safe(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())


def logo_path(ticker: str) -> str:
    return os.path.join(LOGOS_DIR, _safe(ticker) + ".png")


def _missing_marker(ticker: str) -> str:
    return os.path.join(LOGOS_DIR, _safe(ticker) + ".none")


def _get(url: str, min_bytes: int) -> Optional[bytes]:
    import requests
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and r.headers.get("content-type", "").startswith("image") and len(r.content) >= min_bytes:
            return r.content
    except Exception:
        pass
    return None


def _website(ticker: str) -> Optional[str]:
    try:
        import yfinance as yf
        site = (yf.Ticker(ticker).info or {}).get("website")
    except Exception:
        return None
    m = re.match(r"https?://(?:www\.)?([^/]+)", site or "")
    return m.group(1) if m else None


def ensure_logo(ticker: str) -> Optional[str]:
    """Ruta del logo (descargándolo la primera vez) o None si no hay."""
    path = logo_path(ticker)
    if os.path.exists(path):
        return path
    marker = _missing_marker(ticker)
    if os.path.exists(marker) and time.time() - os.path.getmtime(marker) < RETRY_MISSING_DAYS * 86400:
        return None
    os.makedirs(LOGOS_DIR, exist_ok=True)
    data = _get(f"https://financialmodelingprep.com/image-stock/{ticker.upper()}.png", FMP_MIN_BYTES)
    if data is None:
        domain = _website(ticker)
        if domain:
            data = (_get(f"https://icons.duckduckgo.com/ip3/{domain}.ico", ICON_MIN_BYTES)
                    or _get(f"https://www.google.com/s2/favicons?domain={domain}&sz=128", ICON_MIN_BYTES))
    if data is None:
        open(marker, "w").close()
        logger.info("Sin logo para %s", ticker)
        return None
    with open(path, "wb") as f:
        f.write(data)
    return path


def save_upload(ticker: str, content: bytes) -> str:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("La imagen pesa más de 2 MB")
    os.makedirs(LOGOS_DIR, exist_ok=True)
    path = logo_path(ticker)
    with open(path, "wb") as f:
        f.write(content)
    if os.path.exists(_missing_marker(ticker)):
        os.remove(_missing_marker(ticker))
    return path
