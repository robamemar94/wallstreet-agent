"""yf.download, de una en una.

yfinance guarda los resultados de cada yf.download en un almacén global compartido (shared._DFS): dos descargas a la vez
en hilos distintos se mezclan (una se queda con los datos de la otra). Pasó el 16/10/2026: la del pre/post-market (2 días
de barras de 5 min) se cruzó con la del histórico de 18 meses, las posiciones se quedaron sin precio al inicio de los
periodos y salieron 11 avisos falsos de «nuevo máximo/mínimo de 52 semanas». Con install() (al arrancar, en main.py)
todas las llamadas a yf.download de la app van en fila. Ticker(...).history no usa ese almacén y no hace falta.
"""
import functools
import threading

import yfinance as yf

_lock = threading.RLock()


def install() -> None:
    if getattr(yf.download, "_serialized", False):
        return
    original = yf.download

    @functools.wraps(original)
    def download(*args, **kwargs):
        with _lock:
            return original(*args, **kwargs)
    download._serialized = True
    yf.download = download


install()
