"""Comparador de empresas con IA (/comparar)."""
import logging
import os
import yfinance as yf
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBAsset
from app.infrastructure.dependencies import (
    get_asset_repository,
    get_settings_from_db,
)
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.templates import templates


logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Comparar Views"])


@router.get("/comparar", response_class=HTMLResponse)
def comparar_page(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db),
):
    raw_t = request.query_params.getlist("t")
    selected = [t.upper() for t in raw_t[:4] if t.strip()]

    comparisons = []
    for ticker in selected:
        data = asset_repo.get_asset_data(ticker)
        name = data.get("company_name") or ticker
        # Extract numeric scores
        def parse_score(s):
            try:
                return float(str(s).split("/")[0].strip())
            except Exception:
                return None
        try:
            stock = yf.Ticker(ticker)
            price = stock.fast_info.get("lastPrice") or stock.fast_info.get("last_price")
        except Exception:
            price = None
        comparisons.append({
            "ticker": ticker,
            "name": name,
            "sector": data.get("sector", "—"),
            "country": data.get("country", "—"),
            "currency": data.get("currency", "USD"),
            "status": data.get("status", "—"),
            "price": round(price, 2) if price else None,
            "fwd_pe": data.get("last_market_fwd_pe"),
            "fair_pe": (data.get("fair_pe_data") or {}).get("fair_forward_pe"),
            "fin_score": parse_score(data.get("audit_score")),
            "cap_score": parse_score(data.get("mgmt_alloc_score")),
            "moat_score": parse_score(data.get("franchise_score")),
            "verdict_score": parse_score(data.get("verdict_score")),
            "fin_pros": (data.get("audit_pros") or [])[:3],
            "fin_cons": (data.get("audit_cons") or [])[:2],
            "moat_pros": (data.get("franchise_pros") or [])[:2],
            "verdict_pros": (data.get("verdict_pros") or [])[:2],
            "verdict_cons": (data.get("verdict_cons") or [])[:2],
        })

    all_tickers = sorted([
        {"ticker": a.ticker, "name": (a.data or {}).get("company_name") or a.company_name or a.ticker}
        for a in db_session.query(DBAsset).all()
    ], key=lambda x: x["ticker"])
    
    # ── CARGA DEL HISTORIAL DE COMPARACIONES IA (ARCHIVO) ──
    import json
    
    comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
    os.makedirs(comparisons_dir, exist_ok=True)
    
    archived_comparisons = []
    for fname in os.listdir(comparisons_dir):
        if fname.endswith(".json") and fname.startswith("comparison_"):
            try:
                with open(os.path.join(comparisons_dir, fname), "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    archived_comparisons.append({
                        "id": cdata.get("id"),
                        "timestamp": cdata.get("timestamp"),
                        "tickers": cdata.get("tickers", []),
                        "prompt": cdata.get("prompt", ""),
                        "filename": fname
                    })
            except Exception:
                pass
                
    # Sort archived comparisons by timestamp descending
    archived_comparisons.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    loaded_comparison = None
    archive_id = request.query_params.get("archive_id")
    if archive_id:
        for arch in archived_comparisons:
            if arch["id"] == archive_id:
                try:
                    with open(os.path.join(comparisons_dir, arch["filename"]), "r", encoding="utf-8") as f:
                        loaded_comparison = json.load(f)
                        import markdown
                        loaded_comparison["analysis_html"] = markdown.markdown(loaded_comparison.get("analysis", ""), extensions=['tables', 'fenced_code'])
                except Exception:
                    pass

    return templates.TemplateResponse("comparar.html", {
        "request": request,
        "comparisons": comparisons,
        "selected": selected,
        "all_tickers": all_tickers,
        "settings": get_settings_from_db(db_session),
        "archived_comparisons": archived_comparisons,
        "loaded_comparison": loaded_comparison,
    })


@router.post("/api/comparar/generate")
async def generate_comparison(
    request: Request,
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    db_session: Session = Depends(get_db)
):
    import uuid
    import json
    from datetime import datetime
    from google import genai
    from google.genai import types
    
    try:
        body = await request.json()
        tickers = [t.upper() for t in body.get("tickers", []) if t.strip()]
        prompt_text = body.get("prompt", "").strip()
        
        if not tickers:
            return {"status": "error", "message": "No se han seleccionado tickers para comparar."}
            
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"status": "error", "message": "GEMINI_API_KEY no configurada."}
            
        # Gather full details for each ticker
        ticker_details = []
        for ticker in tickers:
            db_d = asset_repo.get_asset_data(ticker)
            
            # Format raw financial numbers as a structured text block
            fin_numbers = ""
            for statement_key, statement_name in [
                ("income_statement", "Cuenta de Resultados (P&L)"),
                ("balance_sheet", "Balance de Situación"),
                ("cash_flow", "Flujo de Caja (Cash Flow)")
            ]:
                st = db_d.get(statement_key, {})
                if st:
                    fin_numbers += f"  - {statement_name}:\n"
                    for metric, yearly_vals in sorted(st.items()):
                        vals_str = ", ".join([f"{yr}: {val}" for yr, val in sorted(yearly_vals.items())])
                        fin_numbers += f"    * {metric}: {vals_str}\n"
            
            ticker_details.append({
                "ticker": ticker,
                "name": db_d.get("company_name", ticker),
                "sector": db_d.get("sector", "—"),
                "country": db_d.get("country", "—"),
                "currency": db_d.get("currency", "USD"),
                "status": db_d.get("status", "—"),
                "fwd_pe": db_d.get("last_market_fwd_pe"),
                "financial_tables_text": fin_numbers,
                "verdict_report": db_d.get("verdict", ""),
                "thesis_report": db_d.get("thesis", ""),
            })
            
        # Construct the context block for Gemini without pre-existing scores or Fair P/E
        context = "SISTEMA DE ANÁLISIS DE CARTERA - COMPARACIÓN MULTI-ACTIVO DE CALIDAD PURA (SIN SESGO NI VALORACIÓN)\n\n"
        for td in ticker_details:
            context += f"=== DATOS E INFORMES DE: {td['ticker']} ({td['name']}) ===\n"
            context += f"Sector: {td['sector']} | País: {td['country']} | Divisa: {td['currency']}\n"
            context += f"Referencia de Mercado: Forward P/E actual: {td['fwd_pe']}x\n\n"
            
            if td['financial_tables_text']:
                context += f"--- TABLAS FINANCIERAS HISTÓRICAS (DATOS NUMÉRICOS REALES) ---\n{td['financial_tables_text']}\n\n"
                
            if td['verdict_report']:
                context += f"--- INFORME DE ANÁLISIS DE CALIDAD ---\n{td['verdict_report'][:3500]}\n\n"
                
            if td['thesis_report']:
                context += f"--- INFORME DE TESIS Y MODELO DE NEGOCIO ---\n{td['thesis_report'][:3500]}\n\n"
            context += "===================================================\n\n"
            
        system_prompt = """Eres el Director de Inversiones de un fondo de inversión de capital riesgo y de inversión en calidad empresarial (Quality/Moat Investing) ultra-exigente. 
Tu objetivo es realizar un análisis comparativo totalmente enfocado en la CALIDAD DEL NEGOCIO de las empresas proporcionadas, sin dejarte influenciar por valoraciones, precios de mercado o múltiplos (salvo como simple referencia del Forward P/E actual). 

ATENCIÓN CRÍTICA: NO debes emitir ninguna opinión sobre si las acciones están caras o baratas, ni estimar PERs Justos, ni calcular márgenes de seguridad, descuentos o primas de valoración. El análisis debe ser 100% sobre la EXCELENCIA EMPRESARIAL PURA, evaluando y comparando las ventajas de los negocios y su solidez financiera.

PAUTAS DEL INFORME COMPARATIVO:
1. Sé extremadamente analítico, riguroso, cuantitativo y crítico. Evita vaguedades y rodeos corporativos.
2. ANALIZA LAS CIFRAS REALES DE CRECIMIENTO Y RENTABILIDAD: Evalúa y compara numéricamente la evolución histórica, estabilidad y tendencias de las partidas financieras de cada empresa (Ingresos, Margen Bruto, Margen Operativo, Beneficio Neto, Free Cash Flow, etc.) que aparecen en el contexto, primando el crecimiento y la consistencia.
3. EVALÚA EL FOSO DEFENSIVO (MOAT): Determina la robustez del modelo de negocio, las barreras de entrada (foso competitivo), la estabilidad del foso y la eficiencia de la asignación de capital del management (ROIC, reinversión) a partir de los informes textuales provistos.
4. EMITE TUS PROPIAS NOTAS DE CALIDAD (SÉ CRÍTICO): Evalúa y emite tu propio criterio de puntuación de 1 a 10 para cada empresa en base a tres conceptos clave: (a) Franquicia y Moat, (b) Auditoría Financiera Forense, y (c) Dirección y Capital Allocation. Justifica tus notas en una tabla comparativa resumen dentro del informe.
5. EMITE UN VEREDICTO DE CALIDAD DEL NEGOCIO: Decreta cuál de las opciones tiene el negocio de mayor calidad absoluta para el horizonte de largo plazo (con mayor foso, solidez financiera, potencial de crecimiento y rentabilidad), justificándolo con argumentos puramente empresariales, operativos y financieros, con total independencia del precio de la acción en el mercado.
6. Estructura tu respuesta en un formato Markdown impecable, usando títulos claros, listas, negritas, y tablas comparativas de resumen para una lectura ejecutiva perfecta."""

        user_instruction = f"""Recuerda: Nuestra filosofía de inversión está orientada estrictamente al LARGO PLAZO, buscando la EXCELENCIA EN LA CALIDAD DEL NEGOCIO y el POTENCIAL DE CRECIMIENTO DE BENEFICIOS FUTUROS Y FLUJO DE CAJA (compounders de calidad).

Por favor, realiza la comparación y el veredicto basándote en el siguiente prompt o instrucción del usuario:
{prompt_text}

Aquí tienes los datos e informes contextuales de las empresas seleccionadas para analizar (evalúa minuciosamente las cifras contables reales y los argumentos de foso descritos, sin calificaciones previas):
{context}"""

        # Call Gemini
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            contents=user_instruction,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2
            )
        )
        
        analysis_markdown = response.text.strip()
        
        # Save comparison
        comp_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        comparison_record = {
            "id": comp_id,
            "timestamp": timestamp,
            "tickers": tickers,
            "prompt": prompt_text,
            "analysis": analysis_markdown
        }
        
        comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
        os.makedirs(comparisons_dir, exist_ok=True)
        with open(os.path.join(comparisons_dir, f"comparison_{comp_id}.json"), "w", encoding="utf-8") as f:
            json.dump(comparison_record, f, ensure_ascii=False, indent=2)
            
        return {"status": "success", "id": comp_id}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/comparar/delete/{comp_id}")
def delete_comparison(comp_id: str):
    comparisons_dir = "/home/rameneiros/wallstreet/agent/data/comparisons"
    fname = f"comparison_{comp_id}.json"
    file_path = os.path.join(comparisons_dir, fname)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "No se encontró el informe en el archivo."}
