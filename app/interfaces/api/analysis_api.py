import logging
import uuid
import markdown
import json
import yfinance as yf
from datetime import datetime
from fastapi import APIRouter, Request, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict
from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db, SessionLocal
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.dependencies import get_asset_repository

import agents as crew_logic
from utils.parsers import parse_summary, parse_sub_report
from tools.search_tool import generate_15y_financials_json, generate_fair_pe_json, parse_manual_audit_to_financials, generate_projection_scenarios_json

from app.infrastructure.db.models import DBTask

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

def get_enriched_audit_context(db_data: dict) -> str:
    audit_text = db_data.get("audit", "")
    
    struct = db_data.get("full_audit_structure")
    if struct and "years" in struct:
        years = struct.get("years", [])
        if years:
            header = "| Métricas | " + " | ".join(years) + " |"
            divider = "| --- | " + " | ".join(["---"] * len(years)) + " |"
            
            sections_md = []
            sections_md.append("\n\n--- ESTADOS FINANCIEROS AUDITADOS Y CALCULADOS (TABLAS DE DATOS) ---")
            
            for section_name in ["income_statement", "balance_sheet", "cash_flow"]:
                section_data = struct.get(section_name, {})
                if not section_data:
                    continue
                    
                pretty_name = section_name.replace("_", " ").title()
                lines = [f"\n### {pretty_name}\n", header, divider]
                
                for metric, values in section_data.items():
                    formatted_values = []
                    for v in values:
                        if v is None:
                            formatted_values.append("N/A")
                        elif isinstance(v, (int, float)):
                            if isinstance(v, float):
                                if abs(v) <= 2.0:
                                    formatted_values.append(f"{v:,.4f}")
                                else:
                                    formatted_values.append(f"{v:,.2f}")
                            else:
                                formatted_values.append(f"{v:,}")
                        else:
                            formatted_values.append(str(v))
                    lines.append(f"| {metric} | " + " | ".join(formatted_values) + " |")
                sections_md.append("\n".join(lines))
                
            return audit_text + "\n" + "\n".join(sections_md)
            
    # Fallback a financials_hist_manual si no existe full_audit_structure
    hist_manual = db_data.get("financials_hist_manual")
    if hist_manual and "years" in hist_manual:
        years = hist_manual.get("years", [])
        if years:
            header = "| Métricas | " + " | ".join(years) + " |"
            divider = "| --- | " + " | ".join(["---"] * len(years)) + " |"
            lines = ["\n\n--- ESTADOS FINANCIEROS HISTÓRICOS MANUALES (TABLAS DE DATOS) ---\n", header, divider]
            for metric, values in hist_manual.items():
                if metric == "years":
                    continue
                formatted_values = []
                for v in values:
                    if v is None:
                        formatted_values.append("N/A")
                    elif isinstance(v, (int, float)):
                        if isinstance(v, float):
                            if abs(v) <= 2.0:
                                formatted_values.append(f"{v:,.4f}")
                            else:
                                formatted_values.append(f"{v:,.2f}")
                        else:
                            formatted_values.append(f"{v:,}")
                    else:
                        formatted_values.append(str(v))
                lines.append(f"| {metric.replace('_', ' ').title()} | " + " | ".join(formatted_values) + " |")
            return audit_text + "\n" + "\n".join(lines)
            
    return audit_text


def run_agent_task(task_id: str, ticker: str, report_type: str, company_name: str, db_data: dict, feedback: str):
    db_session = SessionLocal()
    asset_repo = SqlAlchemyAssetRepository(db_session)
    
    try:
        logger.info(f"Iniciando tarea en background '{report_type}' para {ticker}. Task ID: {task_id}")
        audit_ctx = get_enriched_audit_context(db_data)
        
        if report_type == "audit":
            # Ejecutar el equipo forense (4 analistas) con los datos manuales guardados y el feedback
            manual_raw = json.dumps(db_data.get('manual_audit_raw', {}))
            result = crew_logic.run_forensic_auditor(ticker, company_name, manual_raw, feedback)
            # El resultado ya contiene las 4 secciones unificadas o estructuradas.
            # En una versión futura podríamos guardarlos por separado si CrewAI permite capturar outputs intermedios.
            # Por ahora, guardamos el informe integral bajo 'audit'.
        elif report_type == "franchise":
            result = crew_logic.run_franchise_crew(ticker, company_name, feedback, audit_ctx)
        elif report_type == "mgmt_alloc":
            result = crew_logic.run_management_crew(ticker, company_name, feedback, audit_ctx)
        elif report_type == "verdict":
            result = crew_logic.run_investment_director(
                ticker, 
                company_name, 
                db_data.get("franchise", ""), 
                db_data.get("mgmt_alloc", ""), 
                feedback, 
                audit_ctx, 
                db_data.get("rules", "")
            )
        elif report_type == "val":
            result = crew_logic.run_valuation_agent(ticker, company_name, db_data.get("verdict", ""), feedback, audit_ctx)
        elif report_type == "thesis":
            result = crew_logic.run_thesis_agent(
                ticker,
                company_name,
                db_data.get("verdict", ""),
                db_data.get("val", ""),
                db_data.get("franchise", ""),
                db_data.get("mgmt_alloc", ""),
                feedback,
                audit_ctx,
                db_data.get("rules", "")
            )
        elif report_type == "rules":
            result = crew_logic.run_rules_auditor(ticker, company_name, feedback, audit_ctx)
        elif report_type == "pe_forensic":
            result = crew_logic.run_pe_forensic(ticker, company_name, feedback, audit_ctx)
        elif report_type == "technical":
            result = crew_logic.run_technical_analyst(ticker, company_name, feedback, audit_ctx)
        elif report_type == "thesis_destroyer":
            result = crew_logic.run_thesis_destroyer(
                ticker,
                company_name,
                db_data.get("verdict", ""),
                db_data.get("thesis", ""),
                db_data.get("franchise", ""),
                db_data.get("mgmt_alloc", ""),
                feedback,
                audit_ctx,
                db_data.get("rules", "")
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
            result = crew_logic.run_rules_auditor(ticker, company_name, feedback, audit_ctx, rule_index=idx)
        else:
            update_task_db(db_session, task_id, "error", error="Invalid report type")
            return
            
        main_text, score, pros, cons, rejection = parse_summary(str(result))
        
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
        return {"status": "success", "new_status": status}
    return {"status": "error", "message": "Invalid status"}

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

@router.post("/projections/generate/{ticker}")
def fetch_projection_scenarios(
    ticker: str,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        company_name = info.get('longName') or info.get('shortName') or ticker
        
        # Calcular el dividendo de manera matemáticamente infalible
        div_rate = info.get('dividendRate')
        curr_price = info.get('currentPrice') or info.get('regularMarketPrice') or 1.0
        if div_rate and curr_price and curr_price > 0:
            div_yield = float(div_rate) / float(curr_price)
        else:
            raw_yield = info.get('dividendYield') or 0.0
            if raw_yield > 0.20:
                div_yield = float(raw_yield) / 100.0
            else:
                div_yield = float(raw_yield)
    except Exception:
        company_name = ticker
        div_yield = 0.0
        
    db_data = asset_repo.get_asset_data(ticker)
    pe_data = db_data.get("fair_pe_data", {})
    suggested_pe = pe_data.get("fair_forward_pe")
        
    data = generate_projection_scenarios_json(ticker, company_name, suggested_pe=suggested_pe)
    if "error" in data:
        return {"status": "error", "message": data["error"]}

    # Guardar en la base de datos bajo "projection_scenarios"
    projection_data = {
        "initial_eps": float(data.get("initial_eps", 0)),
        "normalized_eps_explanation": data.get("normalized_eps_explanation", ""),
        "dividend_yield": float(div_yield),
        "bear": {
            "eps_growth": float(data.get("bear", {}).get("eps_growth", 0)),
            "exit_pe": float(data.get("bear", {}).get("exit_pe", 0)),
            "div_growth": float(data.get("bear", {}).get("div_growth", 0))
        },
        "base": {
            "eps_growth": float(data.get("base", {}).get("eps_growth", 0)),
            "exit_pe": float(data.get("base", {}).get("exit_pe", 0)),
            "div_growth": float(data.get("base", {}).get("div_growth", 0))
        },
        "bull": {
            "eps_growth": float(data.get("bull", {}).get("eps_growth", 0)),
            "exit_pe": float(data.get("bull", {}).get("exit_pe", 0)),
            "div_growth": float(data.get("bull", {}).get("div_growth", 0))
        },
        "rationale": data.get("rationale", "Generación con IA."),
        "is_manual": False,
        "last_updated": datetime.now().strftime("%Y-%m-%d")
    }
    asset_repo.save_asset_data(ticker, "projection_scenarios", projection_data)
    return {"status": "success", "data": projection_data}

@router.post("/projections/save/{ticker}")
async def save_projection_scenarios(
    ticker: str,
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    try:
        data = await request.json()
        
        projection_data = {
            "initial_eps": float(data.get("initial_eps", 0)),
            "normalized_eps_explanation": data.get("normalized_eps_explanation", "Ajustado por el usuario."),
            "dividend_yield": float(data.get("dividend_yield") if data.get("dividend_yield") is not None else 0.0),
            "bear": {
                "eps_growth": float(data.get("bear", {}).get("eps_growth", 0)),
                "exit_pe": float(data.get("bear", {}).get("exit_pe", 0)),
                "div_growth": float(data.get("bear", {}).get("div_growth", 0))
            },
            "base": {
                "eps_growth": float(data.get("base", {}).get("eps_growth", 0)),
                "exit_pe": float(data.get("base", {}).get("exit_pe", 0)),
                "div_growth": float(data.get("base", {}).get("div_growth", 0))
            },
            "bull": {
                "eps_growth": float(data.get("bull", {}).get("eps_growth", 0)),
                "exit_pe": float(data.get("bull", {}).get("exit_pe", 0)),
                "div_growth": float(data.get("bull", {}).get("div_growth", 0))
            },
            "rationale": data.get("rationale", "Ajustado manualmente por el usuario."),
            "is_manual": True,
            "last_updated": datetime.now().strftime("%Y-%m-%d")
        }
        
        asset_repo.save_asset_data(ticker, "projection_scenarios", projection_data)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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

@router.post("/audit/extract/{ticker}")
def extract_audit_data(
    ticker: str,
    payload: dict,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository)
):
    """Acción 1: Guarda los datos brutos y extrae el JSON para gráficas/tabla."""
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
    if report_type == "verdict":
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
                "message": "No se puede ejecutar el Veredicto Final porque aún faltan análisis previos por ejecutar. Por favor, realiza primero: " + ", ".join(missing_reports)
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
            
    elif report_type == "thesis_destroyer":
        if not db_data.get("verdict"):
            return {
                "status": "error",
                "message": "No se puede ejecutar el Destructor de Tesis porque aún falta el Veredicto Final. Por favor, ejecuta primero el Veredicto Final (Director de Inversiones)."
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
