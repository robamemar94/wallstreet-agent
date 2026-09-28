"""
Contador de tokens y coste de las llamadas a Gemini.

Todas las llamadas del proyecto (CrewAI, LangChain y las herramientas de search_tool)
acaban pasando por `google.genai` -> `Models.generate_content`. Parcheamos ese punto
único y acumulamos el uso en el "tracker" activo (ContextVar).

Uso:
    with track_usage("AAPL/verdict") as tracker:
        ...  # llamadas a Gemini
    tracker.summary()  # tokens + coste
"""
import contextvars
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Precios en USD por 1M de tokens (tier de pago estándar).
# Fuente: https://ai.google.dev/gemini-api/docs/pricing (consultado 2026-09-24; Pro añadido 2026-09-28).
# Los precios de 3.6/3.7/3.8 Flash son promocionales hasta el 31/12/2026 (se duplican el 01/01/2027).
PRICING = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cached": 0.075},
    "gemini-3.7-flash": {"input": 0.75, "output": 3.75, "cached": 0.075},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "cached": 0.075},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00, "cached": 0.15},
    # Pro: precio para prompts <=200k tokens (por encima se duplica la entrada)
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "cached": 0.20},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached": 0.125},
}
# Grounding con Google Search: 5.000 consultas gratis al mes, después $14 / 1.000 consultas.
SEARCH_FREE_PER_MONTH = 5000
SEARCH_PRICE_PER_QUERY = 14.0 / 1000

USAGE_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "llm_usage.jsonl")

_current: contextvars.ContextVar[Optional["UsageTracker"]] = contextvars.ContextVar("llm_usage_tracker", default=None)
# Trackers de tareas en background. CrewAI ejecuta herramientas en hilos de un ThreadPoolExecutor
# que no heredan el ContextVar: si solo hay una tarea activa, le imputamos esas llamadas.
_fallback_trackers: set = set()
_lock = threading.Lock()
_log_lock = threading.Lock()


def get_price(model: str) -> Optional[dict]:
    name = (model or "").split("/")[-1]
    if name in PRICING:
        return PRICING[name]
    # Versiones con sufijo (ej. gemini-3.8-flash-001, -preview-xx)
    for key in sorted(PRICING, key=len, reverse=True):
        if name.startswith(key):
            return PRICING[key]
    return None


class UsageTracker:
    def __init__(self, label: str = ""):
        self.label = label
        self.calls = 0
        self.input_tokens = 0      # prompt no cacheado + resultados de herramientas (grounding)
        self.cached_tokens = 0
        self.output_tokens = 0     # respuesta visible
        self.thinking_tokens = 0   # "thinking" (se factura como salida)
        self.search_queries = 0
        self.token_cost = 0.0
        self.models = set()
        self.unpriced_models = set()
        self._lock = threading.Lock()

    def record(self, model: str, response) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        model_name = getattr(response, "model_version", None) or model or "desconocido"
        if get_price(model_name) is None and get_price(model):
            model_name = model

        prompt = getattr(usage, "prompt_token_count", 0) or 0
        cached = getattr(usage, "cached_content_token_count", 0) or 0
        tool_prompt = getattr(usage, "tool_use_prompt_token_count", 0) or 0
        output = getattr(usage, "candidates_token_count", 0) or 0
        thinking = getattr(usage, "thoughts_token_count", 0) or 0
        inp = max(prompt - cached, 0) + tool_prompt

        queries = 0
        for cand in getattr(response, "candidates", None) or []:
            gm = getattr(cand, "grounding_metadata", None)
            if gm is not None:
                queries += len(getattr(gm, "web_search_queries", None) or [])

        price = get_price(model_name)
        cost = 0.0
        if price:
            cost = (inp * price["input"] + cached * price["cached"] + (output + thinking) * price["output"]) / 1_000_000

        with self._lock:
            self.calls += 1
            self.input_tokens += inp
            self.cached_tokens += cached
            self.output_tokens += output
            self.thinking_tokens += thinking
            self.search_queries += queries
            self.token_cost += cost
            self.models.add(model_name.split("/")[-1])
            if not price:
                self.unpriced_models.add(model_name.split("/")[-1])

    def summary(self, previous_month_queries: int = 0) -> dict:
        # Solo se cobran las consultas de búsqueda que superan las gratuitas del mes
        billable_before = max(previous_month_queries - SEARCH_FREE_PER_MONTH, 0)
        billable_after = max(previous_month_queries + self.search_queries - SEARCH_FREE_PER_MONTH, 0)
        search_cost = (billable_after - billable_before) * SEARCH_PRICE_PER_QUERY
        return {
            "label": self.label,
            "calls": self.calls,
            "models": sorted(self.models),
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "total_tokens": self.input_tokens + self.cached_tokens + self.output_tokens + self.thinking_tokens,
            "search_queries": self.search_queries,
            "search_queries_month": previous_month_queries + self.search_queries,
            "token_cost_usd": round(self.token_cost, 6),
            "search_cost_usd": round(search_cost, 6),
            "cost_usd": round(self.token_cost + search_cost, 6),
            "unpriced_models": sorted(self.unpriced_models),
        }


def month_search_queries() -> int:
    month = datetime.now().strftime("%Y-%m")
    total = 0
    try:
        with open(USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get("date", "")).startswith(month):
                    total += row.get("search_queries", 0)
    except FileNotFoundError:
        pass
    return total


def _append_log(summary: dict) -> None:
    try:
        os.makedirs(os.path.dirname(USAGE_LOG_PATH), exist_ok=True)
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"No se pudo escribir el log de uso de LLM: {e}")


def _record(model: str, response) -> None:
    tracker = _current.get()
    if tracker is None:
        with _lock:
            if len(_fallback_trackers) == 1:
                tracker = next(iter(_fallback_trackers))
    if tracker is None:
        logger.info(f"Llamada a Gemini ({model}) fuera de cualquier consulta rastreada.")
        return
    try:
        tracker.record(model, response)
    except Exception as e:
        logger.warning(f"No se pudo contabilizar el uso de tokens: {e}")


@contextmanager
def track_usage(label: str = "", background: bool = False):
    """Abre un tracker. Al cerrar, si hubo llamadas, calcula el resumen, lo registra y lo deja en `tracker.result`."""
    tracker = UsageTracker(label)
    tracker.result = None
    token = _current.set(tracker)
    if background:
        with _lock:
            _fallback_trackers.add(tracker)
    try:
        yield tracker
    finally:
        _current.reset(token)
        if background:
            with _lock:
                _fallback_trackers.discard(tracker)
        if tracker.calls:
            with _log_lock:
                summary = tracker.summary(month_search_queries())
                summary["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _append_log(summary)
            tracker.result = summary
            logger.info(
                f"[LLM usage] {label}: {summary['calls']} llamadas, {summary['total_tokens']:,} tokens "
                f"(in {summary['input_tokens']:,} / out {summary['output_tokens']:,} / thinking {summary['thinking_tokens']:,}), "
                f"{summary['search_queries']} búsquedas, coste ${summary['cost_usd']:.4f}"
            )


_installed = False


def install() -> None:
    """Parchea google.genai para contabilizar todas las llamadas. Idempotente."""
    global _installed
    if _installed:
        return
    from google.genai import models as genai_models

    Models, AsyncModels = genai_models.Models, genai_models.AsyncModels
    orig_gc, orig_gcs = Models.generate_content, Models.generate_content_stream
    orig_agc, orig_agcs = AsyncModels.generate_content, AsyncModels.generate_content_stream

    def generate_content(self, *args, **kwargs):
        response = orig_gc(self, *args, **kwargs)
        _record(kwargs.get("model", ""), response)
        return response

    def generate_content_stream(self, *args, **kwargs):
        last = None
        for chunk in orig_gcs(self, *args, **kwargs):
            if getattr(chunk, "usage_metadata", None) is not None:
                last = chunk  # el uso en streaming es acumulado: nos quedamos con el último
            yield chunk
        if last is not None:
            _record(kwargs.get("model", ""), last)

    async def a_generate_content(self, *args, **kwargs):
        response = await orig_agc(self, *args, **kwargs)
        _record(kwargs.get("model", ""), response)
        return response

    async def a_generate_content_stream(self, *args, **kwargs):
        stream = await orig_agcs(self, *args, **kwargs)

        async def wrapped():
            last = None
            async for chunk in stream:
                if getattr(chunk, "usage_metadata", None) is not None:
                    last = chunk
                yield chunk
            if last is not None:
                _record(kwargs.get("model", ""), last)
        return wrapped()

    Models.generate_content = generate_content
    Models.generate_content_stream = generate_content_stream
    AsyncModels.generate_content = a_generate_content
    AsyncModels.generate_content_stream = a_generate_content_stream
    _installed = True
