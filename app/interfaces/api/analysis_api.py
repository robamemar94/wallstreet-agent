import logging
import uuid
import markdown
import json
import yfinance as yf
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db, SessionLocal
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.dependencies import get_asset_repository, get_portfolio_service
from app.application.services.portfolio_service import PortfolioService

import agents as crew_logic
from utils.parsers import parse_summary, parse_sub_report
from tools.search_tool import generate_15y_financials_json, generate_fair_pe_json, parse_manual_audit_to_financials, generate_base_valuation_json
from app.application.services import base_valuation_service, portfolio_roles_service
from app.application.services.thesis_service import quote_unit_factor

from app.infrastructure.db.models import DBTask
from utils.llm_usage import track_usage, month_search_queries

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Analysis"])

def update_task_db(db: Session, task_id: str, status: str, result: str = None, error: str = None):
    task = db.query(DBTask).filter(DBTask.task_id == task_id).first()
    if task:
        task.status = status
        task.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if result:
            task.result_html = result
        if error:
            task.error_message = error
        db.commit()

class GenerateRequest(BaseModel):
    feedback: Optional[str] = None

MAX_CONTEXT_YEARS = 10   # ejercicios completos que ven los agentes (más el LTM): ahorra tokens sin perder el ciclo


def _ctx_value(v) -> str:
    """Cifras grandes en millones con 1 decimal (las tablas son lo que más tokens gasta); el resto, tal cual."""
    if v is None:
        return "N/A"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if abs(v) >= 100_000:
        return f"{v / 1e6:,.1f}"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:,.2f}"
    return f"{v:,}"


def _ctx_table(title: str, years: list, rows: dict, keep: list) -> str:
    lines = [f"\n### {title}\n", "| Métricas | " + " | ".join(str(years[i]) for i in keep) + " |",
             "| --- |" + " --- |" * len(keep)]
    for metric, values in rows.items():
        if not isinstance(values, list):
            continue
        vals = [values[i] if i < len(values) else None for i in keep]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {metric} | " + " | ".join(_ctx_value(v) for v in vals) + " |")
    return "\n".join(lines)


def _kept_years(years: list) -> list:
    """Índices de los últimos MAX_CONTEXT_YEARS ejercicios completos y del LTM."""
    full = [i for i, y in enumerate(years) if str(y).upper() != "LTM"][-MAX_CONTEXT_YEARS:]
    return full + [i for i, y in enumerate(years) if str(y).upper() == "LTM"]


STATEMENTS = ("income_statement", "balance_sheet", "cash_flow")
# Las cuentas que necesita cada pilar de las Normas de Calidad (los ratios y las métricas van siempre)
PILLAR_STATEMENTS = {
    0: ("income_statement",),                    # foso: márgenes
    1: ("income_statement", "cash_flow"),        # crecimiento: ventas, BPA, FCF
    2: STATEMENTS,                               # ROIC: las tres
    3: ("balance_sheet", "cash_flow"),           # solvencia
    4: ("balance_sheet", "cash_flow"),           # asignación de capital: recompras, SBC, M&A, fondo de comercio
}


def financial_tables_context(db_data: dict, sections: tuple = STATEMENTS) -> str:
    """Las cuentas pedidas (en millones, últimos 10 ejercicios + LTM) y los ratios forenses calculados en Python."""
    from app.application.services.forensic_ratios_service import ratios_markdown

    struct = db_data.get("full_audit_structure")
    if struct and struct.get("years"):
        years = struct["years"]
        keep = _kept_years(years)
        parts = ["\n\n--- ESTADOS FINANCIEROS (cifras monetarias en millones; márgenes en tanto por uno) ---"]
        for section_name in sections:
            if struct.get(section_name):
                parts.append(_ctx_table(section_name.replace("_", " ").title(), years, struct[section_name], keep))
        ratios = ratios_markdown(struct, MAX_CONTEXT_YEARS)
        if ratios:
            parts.append("\n" + ratios)
        return "\n".join(parts)

    # Fallback a financials_hist_manual si no existe full_audit_structure
    hist_manual = db_data.get("financials_hist_manual")
    if hist_manual and hist_manual.get("years"):
        years = hist_manual["years"]
        rows = {k.replace("_", " ").title(): v for k, v in hist_manual.items() if k != "years"}
        return ("\n\n--- ESTADOS FINANCIEROS HISTÓRICOS MANUALES (cifras monetarias en millones) ---"
                + _ctx_table("Histórico", years, rows, _kept_years(years)))
    return ""


def get_enriched_audit_context(db_data: dict) -> str:
    """Lo que ven los agentes como «auditoría»: la síntesis forense + las cuentas + los ratios forenses."""
    return (db_data.get("audit", "") or "") + "\n" + financial_tables_context(db_data)


def pillar_score(raw) -> str:
    """Nota de un pilar siempre como «X.X/2.0» (el parser general le añade «/10» y algunos agentes ponían espacios)."""
    import re
    m = re.match(r"\s*([0-9]+(?:[.,][0-9]+)?)", str(raw or ""))
    if not m:
        return raw
    v = float(m.group(1).replace(",", "."))
    return f"{v:.1f}/2.0" if v <= 2.0 else raw


def rules_context(db_data: dict) -> str:
    """Las Normas de Calidad para otros agentes: el informe único antiguo (`rules`) si existe; si no, los 5 pilares
    (nota, fortalezas, debilidades y motivo de descarte), que es lo que se genera ahora."""
    from app.application.services import rules_synthesis_service as rs

    if (db_data.get("rules") or "").strip():
        return db_data["rules"]
    items = rs.pillars(db_data)
    if not items:
        return "(Las Normas de Calidad aún no se han evaluado.)"
    total = sum(p["score"] or 0 for p in items)
    out = [f"Puntuación: {total:.1f} / {rs.MAX_POINTS * len(items):.1f} ({len(items)} de {len(rs.PILLARS)} pilares evaluados)"]
    for p in items:
        score = f"{p['score']:.1f}/{rs.MAX_POINTS:.1f}" if p["score"] is not None else "N/A"
        out.append(f"\nPilar {p['n']} — {p['name']}: {score}")
        if db_data.get(f"rules_pilar{p['n']}_rejection"):
            out.append(f"🛑 {db_data[f'rules_pilar{p['n']}_rejection']}")
        out += [f"+ {x}" for x in p["pros"]] + [f"− {x}" for x in p["cons"]]
    return "\n".join(out)


# Secciones (## …) que se conservan de cada informe al pasarlo condensado a otro agente. La última sección lleva
# el «Resumen del Agente» (nota, pros y contras), así que también se conserva.
KEY_SECTIONS = {
    "franchise": ("MODELO DE NEGOCIO", "PRUEBA EMPÍRICA", "FUENTES DEL ECONOMIC MOAT", "TEST NOKIA", "PUNTUACIÓN DE FRANQUICIA"),
    "mgmt_alloc": ("PUNTUACIÓN DE DIRECCIÓN",),
    "verdict": ("EVALUACIÓN DE RIESGO", "SCORE FINAL", "VEREDICTO DE FILTRO"),
    "thesis_destroyer": ("VEREDICTO DEL ABOGADO", "TESIS DESTRUCTIVAS", "MATRIZ DE ROBUSTEZ"),
}


def condensed_report(db_data: dict, key: str) -> str:
    """Un informe previo resumido para otro agente: su motivo de descarte y sus secciones clave (con el resumen).

    Sin informe, cadena vacía; sin secciones reconocibles (formato antiguo), el informe entero."""
    import re
    text = db_data.get(key) or ""
    if not text.strip():
        return ""
    wanted = KEY_SECTIONS.get(key, ())
    chunks = re.split(r"(?m)^(?=## )", text)
    keep = [c.strip() for c in chunks if any(w.lower() in c.split("\n", 1)[0].lower() for w in wanted)]
    if "Resumen del Agente" in chunks[-1] and chunks[-1].strip() not in keep:
        keep.append("### Resumen del Agente" + chunks[-1].split("### Resumen del Agente", 1)[1])
    body = "\n\n".join(keep) if keep else text
    rejection = db_data.get(f"{key}_rejection")
    return (f"🛑 Motivo de descarte: {rejection}\n\n" if rejection else "") + body


# Tareas que este proceso está ejecutando ahora mismo. Una tarea «processing» en BBDD que no está aquí se quedó
# a medias (el servidor se reinició mientras se generaba) y no va a terminar nunca.
RUNNING_TASKS: set[str] = set()


def running_tasks(db: Session, ticker: str) -> dict:
    """Informes en curso de un ticker, {report_type: task_id}, para que la ficha siga esperándolos al recargar.
    Los que se quedaron a medias por un reinicio se marcan como error, para que se puedan volver a lanzar."""
    out = {}
    for task in db.query(DBTask).filter(DBTask.ticker == ticker.upper(), DBTask.status == "processing").order_by(DBTask.created_at):
        if task.task_id in RUNNING_TASKS:
            out[task.report_type] = task.task_id
        else:
            update_task_db(db, task.task_id, "error", error="Se interrumpió: el servidor se reinició mientras se generaba.")
    return out


def run_agent_task(task_id: str, ticker: str, report_type: str, company_name: str, db_data: dict, feedback: str):
    # Contabilizar tokens y coste de toda la ejecución del agente (incluidas sus herramientas)
    try:
        with track_usage(f"{ticker}/{report_type}", background=True) as tracker:
            _run_agent_task(task_id, ticker, report_type, company_name, db_data, feedback, tracker)
    finally:
        RUNNING_TASKS.discard(task_id)

def _auto_destroyer(asset_repo, ticker: str, company_name: str, db_data: dict, audit_ctx: str, rules_ctx: str) -> str:
    """Genera y guarda el abogado del diablo (como si se hubiera pulsado su botón) y devuelve su contexto condensado.
    Si falla, el veredicto sigue sin él. Su consumo de IA cuenta dentro del del veredicto."""
    try:
        logger.info(f"Veredicto de {ticker} sin abogado del diablo: se genera antes")
        raw = crew_logic.run_thesis_destroyer(ticker, company_name, "", "", db_data.get("franchise", ""),
                                              db_data.get("mgmt_alloc", ""), "", audit_ctx, rules_ctx, "")
        text, score, pros, cons, rejection = parse_summary(str(raw))
        saved = {"thesis_destroyer": text, "thesis_destroyer_score": score, "thesis_destroyer_pros": pros,
                 "thesis_destroyer_cons": cons}
        if rejection:
            saved["thesis_destroyer_rejection"] = rejection
        for k, v in saved.items():
            asset_repo.save_asset_data(ticker, k, v)
        return condensed_report({**db_data, **saved}, "thesis_destroyer")
    except Exception as e:
        logger.warning(f"No se pudo generar el abogado del diablo antes del veredicto de {ticker}: {e}")
        return ""


def _run_agent_task(task_id: str, ticker: str, report_type: str, company_name: str, db_data: dict, feedback: str, tracker):
    db_session = SessionLocal()
    asset_repo = SqlAlchemyAssetRepository(db_session)
    
    try:
        logger.info(f"Iniciando tarea en background '{report_type}' para {ticker}. Task ID: {task_id}")
        audit_ctx = get_enriched_audit_context(db_data)
        # El stress-test del abogado del diablo, si ya existe: lo tienen en cuenta director, valoración y tesis
        destroyer_ctx = condensed_report(db_data, "thesis_destroyer")
        rules_ctx = rules_context(db_data)

        if report_type == "audit":
            # Equipo forense (3 analistas + síntesis) con las cuentas pegadas y los ratios calculados en Python
            from app.application.services.forensic_ratios_service import ratios_markdown
            manual_raw = json.dumps(db_data.get('manual_audit_raw', {}))
            ratios = ratios_markdown(db_data.get("full_audit_structure"), MAX_CONTEXT_YEARS)
            result = crew_logic.run_forensic_auditor(ticker, company_name, manual_raw, feedback, ratios)
        elif report_type == "franchise":
            from app.application.services.forensic_ratios_service import ratios_markdown
            result = crew_logic.run_franchise_crew(ticker, company_name, feedback, audit_ctx,
                                                   ratios_markdown(db_data.get("full_audit_structure"), MAX_CONTEXT_YEARS))
        elif report_type == "mgmt_alloc":
            result = crew_logic.run_management_crew(ticker, company_name, feedback, audit_ctx)
        elif report_type == "verdict":
            # Sin stress-test todavía: el mismo botón lo genera antes, para que el director lo tenga en cuenta
            if not destroyer_ctx:
                destroyer_ctx = _auto_destroyer(asset_repo, ticker, company_name, db_data, audit_ctx, rules_ctx)
            # El director integra: recibe los informes enteros
            result = crew_logic.run_investment_director(
                ticker,
                company_name,
                db_data.get("franchise", ""),
                db_data.get("mgmt_alloc", ""),
                feedback,
                audit_ctx,
                rules_ctx,
                destroyer_ctx,
            )
        elif report_type == "val":
            result = crew_logic.run_valuation_agent(ticker, company_name, db_data.get("verdict", ""), feedback, audit_ctx,
                                                    destroyer_ctx)
        elif report_type == "thesis":
            # Aguas abajo del director: franquicia y dirección condensadas (el veredicto ya las integra)
            result = crew_logic.run_thesis_agent(
                ticker,
                company_name,
                db_data.get("verdict", ""),
                db_data.get("val", ""),
                condensed_report(db_data, "franchise"),
                condensed_report(db_data, "mgmt_alloc"),
                feedback,
                audit_ctx,
                rules_ctx,
                destroyer_ctx,
            )
        elif report_type == "rules":
            from app.application.services.forensic_ratios_service import pillar_metrics_markdown
            result = crew_logic.run_rules_auditor(ticker, company_name, feedback, audit_ctx,
                                                  pillar_metrics=pillar_metrics_markdown(db_data.get("full_audit_structure")))
        elif report_type == "pe_forensic":
            result = crew_logic.run_pe_forensic(ticker, company_name, feedback, audit_ctx)
        elif report_type == "technical":
            result = crew_logic.run_technical_analyst(ticker, company_name, feedback, audit_ctx)
        elif report_type == "thesis_destroyer":
            # Va antes del veredicto: ataca los análisis de calidad (enteros); veredicto, tesis y valoración, si ya existen
            result = crew_logic.run_thesis_destroyer(
                ticker,
                company_name,
                db_data.get("verdict", ""),
                db_data.get("thesis", ""),
                db_data.get("franchise", ""),
                db_data.get("mgmt_alloc", ""),
                feedback,
                audit_ctx,
                rules_ctx,
                db_data.get("val", ""),
            )
        elif report_type in ["rules_pilar1", "rules_pilar2", "rules_pilar3", "rules_pilar4", "rules_pilar5"]:
            rule_indices = {
                "rules_pilar1": 0,
                "rules_pilar2": 1,
                "rules_pilar3": 2,
                "rules_pilar4": 3,
                "rules_pilar5": 4
            }
            idx = rule_indices[report_type]
            from app.application.services.forensic_ratios_service import pillar_metrics_markdown
            pillar_ctx = (db_data.get("audit", "") or "") + "\n" + financial_tables_context(db_data, PILLAR_STATEMENTS[idx])
            result = crew_logic.run_rules_auditor(ticker, company_name, feedback, pillar_ctx, rule_index=idx,
                                                  pillar_metrics=pillar_metrics_markdown(db_data.get("full_audit_structure")))
        else:
            update_task_db(db_session, task_id, "error", error="Invalid report type")
            return
            
        main_text, score, pros, cons, rejection = parse_summary(str(result))
        if report_type.startswith("rules_pilar"):
            score = pillar_score(score)
        
        # Lógica especial para auditoría: separar en sub-informes si tiene delimitadores
        if report_type == "audit" and "---AUDIT_" in main_text:
            try:
                parts = main_text.split("---AUDIT_")
                sections = {}
                for part in parts:
                    if not part.strip(): continue
                    name_header = part.split("---\n")[0].lower()
                    content = part.split("---\n", 1)[1]
                    sections[name_header] = content
                
                # Guardamos los sub-informes específicos en la DB (dentro del JSON del asset)
                inc_text = sections.get("income", "")
                bal_text = sections.get("balance", "")
                csh_text = sections.get("cashflow", "")
                
                asset_repo.save_asset_data(ticker, "audit_income", inc_text)
                asset_repo.save_asset_data(ticker, "audit_balance", bal_text)
                asset_repo.save_asset_data(ticker, "audit_cashflow", csh_text)
                
                # Parsear e integrar scores, pros y cons de cada sub-informe
                inc_score, inc_pros, inc_cons = parse_sub_report(inc_text)
                bal_score, bal_pros, bal_cons = parse_sub_report(bal_text)
                csh_score, csh_pros, csh_cons = parse_sub_report(csh_text)
                
                asset_repo.save_asset_data(ticker, "audit_income_score", inc_score)
                asset_repo.save_asset_data(ticker, "audit_income_pros", inc_pros)
                asset_repo.save_asset_data(ticker, "audit_income_cons", inc_cons)
                
                asset_repo.save_asset_data(ticker, "audit_balance_score", bal_score)
                asset_repo.save_asset_data(ticker, "audit_balance_pros", bal_pros)
                asset_repo.save_asset_data(ticker, "audit_balance_cons", bal_cons)
                
                asset_repo.save_asset_data(ticker, "audit_cashflow_score", csh_score)
                asset_repo.save_asset_data(ticker, "audit_cashflow_pros", csh_pros)
                asset_repo.save_asset_data(ticker, "audit_cashflow_cons", csh_cons)
                
                # El 'audit' principal será la síntesis
                main_text = sections.get("synthesis", main_text)
            except Exception as e:
                logger.warning(f"No se pudieron separar o parsear las secciones de auditoría: {e}")

        asset_repo.save_asset_data(ticker, report_type, main_text)
        asset_repo.save_asset_data(ticker, f"{report_type}_score", score)
        asset_repo.save_asset_data(ticker, f"{report_type}_pros", pros)
        asset_repo.save_asset_data(ticker, f"{report_type}_cons", cons)
        if rejection:
            asset_repo.save_asset_data(ticker, f"{report_type}_rejection", rejection)
        
        logger.info(f"Reporte '{report_type}' generado exitosamente para {ticker}")
        html_result = markdown.markdown(main_text, extensions=['tables', 'fenced_code'])
        # Guardar tokens y coste de esta ejecución para mostrarlos en la UI
        asset_repo.save_asset_data(ticker, f"{report_type}_usage", tracker.summary(month_search_queries()))
        update_task_db(db_session, task_id, "success", result=html_result)
        
    except Exception as e:
        logger.error(f"Error en background para '{report_type}' de {ticker}: {e}", exc_info=True)
        update_task_db(db_session, task_id, "error", error=str(e))
    finally:
        db_session.close()

@router.post("/status/{ticker}")
async def update_status(
    ticker: str, 
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    data = await request.json()
    status = data.get("status")
    if status in ["ACCEPTED", "REJECTED", "PENDING", "STANDBY"]:
        asset_repo.save_asset_data(ticker, "status", status)
        if status == "REJECTED" and asset_repo.get_asset_data(ticker).get(portfolio_roles_service.ROLE_KEY):
            asset_repo.save_asset_data(ticker, portfolio_roles_service.ROLE_KEY, None)   # descartada: sale del Portfolio ideal
        return {"status": "success", "new_status": status}
    return {"status": "error", "message": "Invalid status"}


@router.post("/role/{ticker}")
async def update_role(
    ticker: str,
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
):
    """Rol en el Portfolio ideal (CORE, GROWTH, QUALITY, OPTIONALITY); role vacío lo quita."""
    role = (await request.json()).get("role") or None
    if role and role not in portfolio_roles_service.role_keys():
        raise HTTPException(status_code=400, detail=f"Rol desconocido: {role}")
    if role:
        held = any(i.ticker == ticker and i.shares > 0 for i in portfolio_service.get_active_portfolio())
        if not portfolio_roles_service.eligible(asset_repo.get_asset_data(ticker), asset_repo.is_favorite(ticker), held):
            raise HTTPException(status_code=400, detail="Solo pueden tener rol las empresas en seguimiento, favoritas o en cartera")
    asset_repo.save_asset_data(ticker, portfolio_roles_service.ROLE_KEY, role)
    return {"status": "success", "role": role}

@router.post("/fetch_15y/{ticker}")
def fetch_15y(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    data = generate_15y_financials_json(ticker)
    if "error" in data:
        return {"status": "error", "message": data["error"]}

    asset_repo.save_asset_data(ticker, "financials_hist_15y", data)
    return {"status": "success"}

@router.post("/rules_synthesis/{ticker}")
def generate_rules_synthesis(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Síntesis de las Normas de Calidad (solo a mano, con los 5 pilares hechos)."""
    from app.application.services import rules_synthesis_service
    data = asset_repo.get_asset_data(ticker) or {}
    try:
        company = (yf.Ticker(ticker).info or {}).get("longName") or ticker
    except Exception:
        company = ticker
    try:
        syn = rules_synthesis_service.generate(ticker, data, company)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.exception("Síntesis de normas de %s", ticker)
        return {"status": "error", "message": str(e)[:200]}
    asset_repo.save_asset_data(ticker, rules_synthesis_service.KEY, syn)
    return {"status": "success", "data": syn}

@router.post("/fair_pe/{ticker}")
def fetch_fair_pe(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    try:
        stock = yf.Ticker(ticker)
        company_name = stock.info.get('longName') or stock.info.get('shortName') or ticker
    except Exception:
        company_name = ticker
        
    data = generate_fair_pe_json(ticker, company_name)
    if "error" in data:
        return {"status": "error", "message": data["error"]}

    data["analysis_date"] = datetime.now().strftime("%d/%m/%Y")
    asset_repo.save_asset_data(ticker, "fair_pe_data", data)
    return {"status": "success"}

@router.post("/fair_pe_manual/{ticker}")
async def edit_fair_pe(
    ticker: str, 
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    data = await request.json()
    new_pe = data.get("fair_forward_pe")
    if new_pe is None:
        return {"status": "error", "message": "Falta fair_forward_pe"}
        
    db_data = asset_repo.get_asset_data(ticker)
    pe_data = db_data.get("fair_pe_data", {})
    
    if not pe_data.get("is_manual") and "fair_forward_pe" in pe_data:
        pe_data["ia_suggested_pe"] = pe_data["fair_forward_pe"]
    
    pe_data["fair_forward_pe"] = float(new_pe)
    pe_data["is_manual"] = True
    pe_data["analysis_date"] = datetime.now().strftime("%d/%m/%Y")
    
    if "historical_pe" not in pe_data:
        pe_data["historical_pe"] = "N/A"
    if "sector_pe" not in pe_data:
        pe_data["sector_pe"] = "N/A"
    if "rationale" not in pe_data:
        pe_data["rationale"] = "Ajustado manualmente por el usuario."
        
    asset_repo.save_asset_data(ticker, "fair_pe_data", pe_data)
    return {"status": "success"}

@router.post("/valuation/{ticker}/generate")
def generate_base_valuation(
    ticker: str,
    metric: str = "eps",
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """La IA propone el caso base (valor de partida, crecimiento, dividendo y múltiplo) para la métrica elegida."""
    try:
        info = yf.Ticker(ticker).info
        company_name = info.get('longName') or info.get('shortName') or ticker
        dps = float(info.get('dividendRate') or 0.0) * quote_unit_factor(info.get('currency'))   # Londres: en peniques
    except Exception:
        company_name, dps = ticker, 0.0

    db_data = asset_repo.get_asset_data(ticker)
    suggested = (db_data.get("fair_pe_data") or {}).get("fair_forward_pe")
    data = generate_base_valuation_json(ticker, company_name, metric=metric, suggested_multiple=suggested)
    if "error" in data:
        return {"status": "error", "message": data["error"]}

    # El valor de partida de la IA queda como sugerencia: por defecto se parte del TTM, que se actualiza solo
    # la corrección de un año que hayas hecho a mano se conserva
    anchor = (base_valuation_service.stored(db_data) or {}).get("anchor")
    valuation = base_valuation_service.normalize({**data, "metric": metric, "dps": dps, "metric_value": None, "anchor": anchor},
                                                 is_manual=False)
    valuation["metric_explanation"] = data.get("metric_explanation", "")
    valuation["metric_suggested"] = float(data["metric_value"]) if data.get("metric_value") else None
    asset_repo.save_asset_data(ticker, base_valuation_service.ASSET_KEY, valuation)
    return {"status": "success", "data": valuation}

@router.post("/valuation/{ticker}")
async def save_base_valuation(
    ticker: str,
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    try:
        data = await request.json()
        valuation = base_valuation_service.normalize(data)
        valuation["metric_explanation"] = data.get("metric_explanation") or ""
        valuation["metric_suggested"] = data.get("metric_suggested")
        asset_repo.save_asset_data(ticker, base_valuation_service.ASSET_KEY, valuation)
        return {"status": "success", "data": valuation}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/valuation/{ticker}/reviewed")
def mark_base_valuation_reviewed(ticker: str, asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)):
    """«Revisada, sin cambios»: reinicia el plazo de revisión sin tocar los supuestos."""
    valuation = asset_repo.get_asset_data(ticker).get(base_valuation_service.ASSET_KEY)
    if not valuation:
        return {"status": "error", "message": "No hay valoración guardada: guárdala para darla por revisada."}
    valuation["reviewed_at"] = datetime.now().strftime("%Y-%m-%d")
    asset_repo.save_asset_data(ticker, base_valuation_service.ASSET_KEY, valuation)
    return {"status": "success", "reviewed_at": valuation["reviewed_at"]}

@router.post("/fair_pe_bulk")
async def bulk_edit_fair_pe(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Actualiza el PE de compra de varios tickers en una sola llamada.
    Body: [{"ticker": "AAPL", "fair_forward_pe": 25.0}, ...]
    """
    items = await request.json()
    updated = 0
    for item in items:
        ticker = item.get("ticker")
        new_pe = item.get("fair_forward_pe")
        if not ticker or new_pe is None:
            continue
        try:
            pe_val = float(new_pe)
            if pe_val <= 0:
                continue
            db_data = asset_repo.get_asset_data(ticker)
            pe_data = db_data.get("fair_pe_data", {})
            if not pe_data.get("is_manual") and "fair_forward_pe" in pe_data:
                pe_data["ia_suggested_pe"] = pe_data["fair_forward_pe"]
            pe_data["fair_forward_pe"] = pe_val
            pe_data["is_manual"] = True
            pe_data["analysis_date"] = datetime.now().strftime("%d/%m/%Y")
            pe_data.setdefault("historical_pe", "N/A")
            pe_data.setdefault("sector_pe", "N/A")
            pe_data.setdefault("rationale", "Ajustado manualmente por el usuario.")
            asset_repo.save_asset_data(ticker, "fair_pe_data", pe_data)
            updated += 1
        except (ValueError, TypeError):
            continue
    return {"status": "success", "updated": updated}


@router.delete("/ticker/{ticker}")
async def delete_ticker(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    success = asset_repo.delete_asset(ticker)
    if success:
        return {"status": "success", "message": f"Ticker {ticker} eliminado correctamente."}
    return {"status": "error", "message": f"No se pudo eliminar el ticker {ticker}."}

# Extracciones de cuentas en curso o recién acabadas, {TICKER: {"status": running|success|error, "message"}}. La
# petición sigue en el servidor aunque se recargue la ficha; así la ficha nueva sabe que hay una y la espera.
EXTRACTIONS: dict[str, dict] = {}


@router.get("/audit/extract_status/{ticker}")
def extract_audit_status(ticker: str):
    return EXTRACTIONS.get(ticker.upper()) or {"status": "none"}


@router.post("/audit/extract/{ticker}")
def extract_audit_data(
    ticker: str,
    payload: dict,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Acción 1: Guarda los datos brutos y extrae el JSON para gráficas/tabla."""
    key = ticker.upper()
    if (EXTRACTIONS.get(key) or {}).get("status") == "running":
        return {"status": "error", "message": "Ya se están extrayendo las cuentas de esta empresa."}
    EXTRACTIONS[key] = {"status": "running"}
    out = _extract_audit_data(ticker, payload, asset_repo)
    EXTRACTIONS[key] = {"status": out["status"], "message": out.get("message")}
    return out


def _extract_audit_data(ticker: str, payload: dict, asset_repo: SqlAlchemyAssetRepository) -> dict:
    try:
        feedback = payload.get("feedback", "")
        import json
        audit_data = json.loads(feedback)
        
        # 1. Guardar datos brutos
        asset_repo.save_asset_data(ticker, "manual_audit_raw", audit_data)
        
        # 2. Extraer números estructurados
        combined_text = f"{audit_data.get('income','')}\n{audit_data.get('balance','')}\n{audit_data.get('cashflow','')}"
        extracted_fin = parse_manual_audit_to_financials(ticker, combined_text, audit_data.get('images'))
        
        if extracted_fin and "years" in extracted_fin:
            # lo nuevo manda en los años que trae; los antiguos que ya tenías y no vienen ahora se conservan
            from app.application.services.financials_update_service import merge
            extracted_fin = merge(asset_repo.get_asset_data(ticker).get("full_audit_structure"), extracted_fin)
            # Guardar la estructura COMPLETA para consulta del usuario
            asset_repo.save_asset_data(ticker, "full_audit_structure", extracted_fin)
            
            # Guardar solo las métricas necesarias para las gráficas existentes
            chart_data = extracted_fin.get("chart_metrics", {})
            chart_data["years"] = extracted_fin["years"]
            asset_repo.save_asset_data(ticker, "financials_hist_manual", chart_data)
            
            return {"status": "success", "message": "Estructura completa extraída correctamente.", "data": extracted_fin}
        
        return {"status": "error", "message": "No se pudieron extraer datos numéricos válidos."}
    except Exception as e:
        logger.error(f"Error en extracción: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/generate/{ticker}/{report_type}")
def generate_report(
    ticker: str, 
    report_type: str, 
    background_tasks: BackgroundTasks, 
    request: Optional[GenerateRequest] = None,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    db_data = asset_repo.get_asset_data(ticker) or {}
    feedback = request.feedback if request else ""
    
    # Validaciones de precondición antes de lanzar tareas en background
    if report_type in ("verdict", "thesis_destroyer"):
        missing_reports = []
        labels = {
            "audit": "Auditoría Financiera Forense",
            "franchise": "Calidad de Franquicia (Negocio y Moat)",
            "mgmt_alloc": "Dirección y Asignación de Capital"
        }
        for k in ["audit", "franchise", "mgmt_alloc"]:
            if not db_data.get(k):
                missing_reports.append(labels[k])
                
        if missing_reports:
            return {
                "status": "error", 
                "message": ("No se puede ejecutar el Veredicto Final" if report_type == "verdict" else "No se puede ejecutar el Abogado del Diablo")
                           + " porque aún faltan análisis previos por ejecutar. Por favor, realiza primero: " + ", ".join(missing_reports)
            }
            
    elif report_type == "val":
        if not db_data.get("verdict"):
            return {
                "status": "error",
                "message": "No se puede ejecutar la Valuación porque aún falta el Veredicto Final. Por favor, ejecuta primero el Veredicto Final (Director de Inversiones)."
            }
            
    elif report_type == "thesis":
        if not db_data.get("verdict"):
            return {
                "status": "error",
                "message": "No se puede ejecutar la Tesis y Vigilancia porque aún falta el Veredicto Final. Por favor, ejecuta primero el Veredicto Final (Director de Inversiones)."
            }
            
            
    try:
        stock = yf.Ticker(ticker)
        company_name = stock.info.get('longName') or stock.info.get('shortName') or ticker
    except Exception:
        company_name = ticker

    task_id = f"{ticker}_{report_type}_{uuid.uuid4().hex[:8]}"
    
    # Determinar si es un informe legacy (sin datos estructurados del Paso 0)
    is_legacy_val = False
    if report_type not in ["audit", "franchise", "technical"]:
        has_struct = db_data.get("full_audit_structure") if db_data else None
        has_manual = db_data.get("financials_hist_manual") if db_data else None
        if not has_struct and not has_manual:
            is_legacy_val = True

    # Create task record in DB
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_task = DBTask(
        task_id=task_id,
        ticker=ticker,
        report_type=report_type,
        status="processing",
        is_legacy=is_legacy_val,
        created_at=now_str,
        updated_at=now_str
    )
    db_session.add(db_task)
    db_session.commit()
    RUNNING_TASKS.add(task_id)
    
    background_tasks.add_task(run_agent_task, task_id, ticker, report_type, company_name, db_data, feedback)
    
    return {"status": "processing", "task_id": task_id}

@router.get("/generate_status/{task_id}")
async def get_generate_status(
    task_id: str,
    db_session: Session = Depends(get_db),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    task = db_session.query(DBTask).filter(DBTask.task_id == task_id).first()
    if not task:
        return {"status": "unknown", "error": "Task not found"}
    
    response = {"status": task.status}
    if task.status == "success":
        response["result"] = task.result_html
        
        # Recover meta information from DBAsset
        asset_data = asset_repo.get_asset_data(task.ticker)
        rt = task.report_type
        response["meta"] = {
            "score": asset_data.get(f"{rt}_score", "N/A"),
            "pros": asset_data.get(f"{rt}_pros", []),
            "cons": asset_data.get(f"{rt}_cons", []),
            "rejection": asset_data.get(f"{rt}_rejection", None),
            "is_legacy": task.is_legacy,
            "date": task.updated_at,
            "financials_hist_manual": asset_data.get("financials_hist_manual")
        }
        response["usage"] = asset_data.get(f"{rt}_usage")

        if rt == "technical" and asset_data.get(rt):
            import re
            raw_text = asset_data[rt]
            traffic_lights = {}
            for row_name, key_name in [
                ("Momentum (RSI/MACD/Bollinger)", "momentum"),
                ("Estructura (SMA/Soportes/Semanal)", "estructura"),
                ("Price Action (Vela/Volumen)", "price_action"),
            ]:
                escaped_name = re.escape(row_name)
                pattern = rf"\|\s*\*?\*?{escaped_name}\*?\*?\s*\|\s*([^|]+)\|\s*([^|]+)\|"
                match = re.search(pattern, raw_text, re.IGNORECASE)
                if match:
                    traffic_lights[key_name] = {
                        "classification": match.group(1).strip().strip('*`').replace('**', ''),
                        "justification": match.group(2).strip().strip('*`').replace('**', ''),
                    }

            # La fila de Puntuación Clínica tiene las columnas invertidas respecto a las demás:
            # 1ª columna = nota numérica (1-10), 2ª columna = frase de justificación.
            pc_pattern = rf"\|\s*\*?\*?{re.escape('PUNTUACIÓN \"CLÍNICA\"')}\*?\*?\s*\|\s*([^|]+)\|\s*([^|]+)\|"
            pc_match = re.search(pc_pattern, raw_text, re.IGNORECASE)
            if pc_match:
                traffic_lights["puntuacion_clinica"] = {
                    "score": pc_match.group(1).strip().strip('*`').replace('**', ''),
                    "justification": pc_match.group(2).strip().strip('*`').replace('**', ''),
                }
            response["meta"]["traffic_lights"] = traffic_lights

            wait_nuance = {}
            bias_match = re.search(r"Sesgo[^*:\n]*:?\*{0,2}\s*\*{0,2}(ALCISTA|NEUTRAL|BAJISTA)", raw_text, re.IGNORECASE)
            if bias_match:
                wait_nuance["bias"] = bias_match.group(1).strip().upper()
            trigger_match = re.search(r"Gatillo de Reevaluaci[oó]n[^*:\n]*:?\*{0,2}\s*(.+)", raw_text, re.IGNORECASE)
            if trigger_match:
                trigger_line = trigger_match.group(1).strip(" *").replace('**', '')
                dist_match = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", trigger_line)
                if dist_match:
                    wait_nuance["distance"] = dist_match.group(0).strip()
                    clause_match = re.search(r"Indica tambi[eé]n|Distancia Actual", trigger_line, re.IGNORECASE)
                    if clause_match:
                        trigger_line = trigger_line[:clause_match.start()].strip(" .:,")
                wait_nuance["trigger"] = trigger_line
            if wait_nuance:
                response["meta"]["wait_nuance"] = wait_nuance
        
        if rt == "audit":
            response["sub_reports"] = {
                "income_html": markdown.markdown(asset_data.get("audit_income", ""), extensions=['tables', 'fenced_code']),
                "balance_html": markdown.markdown(asset_data.get("audit_balance", ""), extensions=['tables', 'fenced_code']),
                "cashflow_html": markdown.markdown(asset_data.get("audit_cashflow", ""), extensions=['tables', 'fenced_code']),
                
                "income_score": asset_data.get("audit_income_score", "N/A"),
                "income_pros": asset_data.get("audit_income_pros", []),
                "income_cons": asset_data.get("audit_income_cons", []),
                
                "balance_score": asset_data.get("audit_balance_score", "N/A"),
                "balance_pros": asset_data.get("audit_balance_pros", []),
                "balance_cons": asset_data.get("audit_balance_cons", []),
                
                "cashflow_score": asset_data.get("audit_cashflow_score", "N/A"),
                "cashflow_pros": asset_data.get("audit_cashflow_pros", []),
                "cashflow_cons": asset_data.get("audit_cashflow_cons", []),
            }
    elif task.status == "error":
        response["error"] = task.error_message
        
    return response

@router.get("/search_suggestions")
async def search_suggestions(
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Retorna sugerencias de búsqueda (ticker + nombre) para el autocompletado."""
    return asset_repo.get_all_companies()

@router.get("/recent_searches")
async def get_recent_searches(
    db: Session = Depends(get_db)
):
    """Retorna los tickers analizados recientemente por agentes."""
    # Get distinct tickers from tasks table, ordered by most recent
    subquery = db.query(DBTask.ticker, func.max(DBTask.updated_at).label('max_updated'))\
                 .group_by(DBTask.ticker)\
                 .order_by(desc('max_updated'))\
                 .limit(10).subquery()
    
    recent = db.query(subquery.c.ticker).all()
    return [r[0] for r in recent]

@router.post("/favorite/{ticker}/toggle")
async def toggle_favorite(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Cambia el estado de favorito de un ticker."""
    is_fav = asset_repo.toggle_favorite(ticker.upper())
    return {"status": "success", "is_favorite": is_fav}
