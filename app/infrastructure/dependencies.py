from fastapi import Depends
from sqlalchemy.orm import Session

from app.infrastructure.db.database import get_db
from app.infrastructure.db.models import DBSetting
from app.infrastructure.repositories.sqlalchemy_asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.repositories.sqlalchemy_portfolio_repository import SqlAlchemyPortfolioRepository
from app.infrastructure.repositories.sqlalchemy_thesis_repository import SqlAlchemyThesisRepository
from app.application.services.portfolio_service import PortfolioService
from app.application.services.performance_service import PerformanceService

DEFAULT_SETTINGS = {
    "ui": {"items_per_page": 10, "dark_mode": False},
    "valuation": {"ganga": 0.60, "barata": 0.85, "justo_max": 1.15, "cara_max": 1.40},
    "labels": {
        "ganga": "Ganga",
        "barata": "Barato",
        "justo": "Precio Justo",
        "cara": "Caro",
        "burbuja": "Burbuja"
    },
    "performance": {"benchmark_ticker": "URTH", "cache_duration_min": 60, "start_date": None},
    "alerts": {"price_drop_threshold": 10},
    "brokers": {
        "degiro_fee_eur": 1.0,
        "degiro_fee_usd": 1.0,
        "degiro_fee_sek": 3.90,
        "degiro_autofx_pct": 0.25,
        "ibkr_fee_usd_per_share": 0.005,
        "ibkr_fee_usd_min": 1.00,
        "ibkr_fee_usd_max_pct": 1.0,
        "ibkr_autofx_usd_min": 2.00,
        "ibkr_autofx_usd_pct": 0.2,
        "ibkr_fee_sek_min": 40.0,
        "ibkr_fee_sek_pct": 0.05,
        "ibkr_fee_eur_min": 1.25,
        "ibkr_fee_eur_pct": 0.05
    },
    "custom_evaluation_rules": [
        {
            "title": "Pilar 1: Foso Competitivo, Pricing Power y Riesgo Terminal",
            "content": "Analiza el PILAR 1: foso competitivo, pricing power y riesgo terminal (0–2.0 puntos).\n\nDatos a usar:\n- Margen bruto anual de los últimos 10 años.\n- Cualquier evidencia de presión competitiva, cambios de mix, inflación de costes o sustitución tecnológica.\n\nTareas:\n1) Evalúa el Margen Bruto. NOTA CRÍTICA: Un margen bruto alto (>50%) es condición necesaria pero NO suficiente para un foso duradero. No califiques alto si el foso estratégico es frágil, depende de modas, carece de costes de cambio reales, o el crecimiento se desacelera severamente.\n2) Evalúa el pricing power histórico y la resiliencia ante inflación o desaceleraciones de ingresos.\n3) Analiza el riesgo de obsolescencia o pérdida de relevancia a 10 años (competencia, regulación o sustitución tecnológica/IA).\n\nCriterio de puntuación (Escala de Grises):\n- 1.6 a 2.0 (Foso Ancho - Wide Moat): Barreras de entrada masivas, altos costes de cambio para el cliente, efectos de red o claras ventajas de escala monopolísticas respaldadas por márgenes excelentes y estables.\n- 1.0 a 1.5 (Foso Estrecho - Narrow Moat): Ventajas reales de nicho o modelo (ej. DTC) pero en un sector abierto e hiper-competitivo, sin costes de cambio reales para el cliente y vulnerable a modas, saturación de canal o coste de adquisición al alza. Si hay desaceleraciones a un dígito (como RVRC.ST), sé riguroso en este rango (1.0 a 1.2 puntos).\n- 0.0 a 0.9 (Sin Foso - No Moat): Producto comoditizado, sin pricing power, márgenes persistentemente por debajo del 50%, o con un riesgo de obsolescencia existencial inminente.\n\nSalida requerida:\n- Tabla con margen bruto por año (últimos 10 años).\n- Diagnóstico estratégico del Moat (Ancho / Estrecho / Sin Foso).\n- Riesgos de obsolescencia y relevancia futura.\n- Puntuación final."
        },
        {
            "title": "Pilar 2: Crecimiento de Ventas, FCF Total y BPA",
            "content": "Analiza el PILAR 2: crecimiento de ventas, FCF Total y Beneficio por Acción (BPA) (0–2.0 puntos).\n\nCalcula:\n- CAGR de ingresos (ventas) a 3, 5 y 10 años.\n- CAGR de FCF Total (no por acción, para medir el crecimiento a escala pura de la caja generada por el negocio) a 3, 5 y 10 años.\n- CAGR de Beneficio por Acción (BPA/EPS) a 3, 5 y 10 años.\n- Evolución del número de acciones en circulación (dilución).\n- Crecimiento orgánico si los datos lo permiten.\n\nTareas:\n1) Evalúa si el crecimiento de ingresos se traduce en crecimiento de FCF Total sólido (crecimiento orgánico de escala del negocio).\n2) Determina si la empresa gana o pierde terreno competitivo basándote en la tracción de sus ventas totales.\n3) REGLAS CRÍTICAS DE EXCELENCIA Y ANÁLISIS DE TENDENCIAS: El ancla principal para calificar con excelencia (2.0 puntos) es el crecimiento a largo plazo (10 años), donde buscamos un crecimiento de ventas >8% CAGR y de FCF Total >10% CAGR. Los periodos intermedios y cortos (3 y 5 años) deben analizarse como tendencias y contexto analítico, no como barreras eliminatorias estrictas. Un bache en el corto plazo es totalmente aceptable si es coyuntural, cíclico o puntual, siempre que el motor de largo plazo siga intacto. Si detectas un deterioro estructural claro e irreversible (pérdida de foso, ventas cayendo permanentemente), califica bajo (0.0 a 0.5 puntos). Pero si el bache es temporal y el negocio sigue fuerte en el largo plazo, otorga una puntuación intermedia o sólida (rango de 1.0 a 1.5 puntos).\n4) NOTA DE DISEÑO: En este pilar evalúas únicamente la trayectoria e incremento de las métricas operativas a escala pura. El análisis forense sobre si la dilución por SBC es abusiva, si se está manipulando artificialmente el BPA con recompras de acciones o si estas destruyen valor se delega de forma exclusiva al Pilar 5.\n\nCriterio de puntuación:\n- 2.0: excelencia con crecimiento a largo plazo robusto (10 años: ventas >8% CAGR, FCF Total >10% CAGR), con tendencias de corto plazo saludables o con desaceleraciones puramente temporales/cíclicas bajo control.\n- 1.0 a 1.5: aprobado con fundamentales de largo plazo sólidos pero ligeramente inferiores a los umbrales, o con un bache temporal/cíclico claro en el corto plazo que deprime temporalmente las CAGR a 3/5 años pero sin deterioro estructural.\n- 0.0 a 0.5: estancamiento estructural irreversible, o deterioro destructivo del negocio en el largo plazo.\n\nSalida requerida:\n- Tabla de métricas de los últimos 10 años (Ingresos, FCF Total, BPA, Acciones en circulación).\n- Diagnóstico del ciclo de crecimiento y tracción operativa.\n- Puntuación final."
        },
        {
            "title": "Pilar 3: ROIC, Márgenes y Eficiencia del Capital",
            "content": "Analiza el PILAR 3: ROIC, márgenes y eficiencia del capital (0–2.0 puntos).\n\nCalcula:\n- ROIC promedio de 10 años.\n- ROIC con goodwill incluido en el capital invertido (últimos 10 años).\n- Margen EBIT medio de 10 años.\n- Cascada de margen de los últimos 10 años: bruto -> EBIT -> FCF.\n\nTareas:\n1) Evalúa la rentabilidad real del capital total invertido, incluyendo goodwill.\n2) Distingue un negocio de baja rentabilidad aparente pero alta rotación de activos de uno realmente mediocre.\n3) Si hay adquisiciones relevantes, separa el efecto base del efecto de integración.\n4) Detecta deterioro estructural de rentabilidad y compáralo con el coste de capital si hay datos.\n5) REGLA DE DISTINCIÓN DE GOODWILL (CRÍTICA - PUNTO MEDIO): Si la empresa es un Serial Acquirer con elevado Goodwill que comprime el ROIC total medio (<15%), pero el ROIC Tangible (sin Goodwill) es excepcional (>20%) y el negocio genera masivo FCF, NO apliques un suspenso o 0.0 automático, pero TAMPOCO concedas la máxima puntuación (2.0). Limita la nota de este pilar a un rango intermedio de 1.0 a 1.5 de 2.0 puntos (Aprobado con advertencia) para dejar reflejado en la nota el arrastre histórico y el riesgo de sobrepago de Goodwill del pasado.\n\nCriterio de puntuación:\n- 2.0: ROIC robusto, de alta calidad, sostenible Y claramente superior al coste de capital (con Goodwill incluido de media >15%).\n- 1.0 a 1.5: ROIC Tangible excelente (>20%) en Serial Acquirers, pero con ROIC total medio comprimido por elevado Goodwill (<15%).\n- 0.0: rentabilidad pobre o destructiva.\n\nSalida requerida:\n- Tabla de ratios de los últimos 10 años.\n- Explicación de la estructura de rentabilidad.\n- Puntuación y juicio operativo."
        },
        {
            "title": "Pilar 4: Solvencia, Liquidez y Riesgo de Refinanciación",
            "content": "Analiza el PILAR 4: solvencia, liquidez y riesgo de refinanciación (0–2.0 puntos).\n\nCalcula (de los últimos 10 años):\n1) Deuda económica total / FCF.\n2) Quick ratio ajustado.\n3) FCF / EBITDA.\n4) Calendario de vencimientos si está disponible.\n\nTareas:\n- Evalúa la capacidad real de la empresa para absorber estrés financiero.\n- Distingue deuda peligrosa de deuda funcional para financiar crecimiento o activos de larga vida.\n- Identifica concentración de cliente, proveedor o contrato que pueda tensar la liquidez.\n- Si FCF / EBITDA es débil, explica si el capex es de mantenimiento, expansión o recuperación.\n\nCriterio de puntuación:\n- 2.0: balance muy resistente y liquidez holgada.\n- 1.0: solvencia aceptable pero con riesgos o dependencia de refinanciación.\n- 0.0: fragilidad financiera seria.\n\nSalida requerida:\n- Tabla de ratios de los últimos 10 años.\n- Riesgos de balance.\n- Puntuación final."
        },
        {
            "title": "Pilar 5: Asignación de Capital, ROIIC, Recompra, SBC y Gobernanza",
            "content": "Analiza el PILAR 5: asignación de capital, ROIIC, recompra, SBC y gobernanza (0–2.0 puntos).\n\nCalcula (de los últimos 10 años):\n- ROIIC de los últimos 3, 5 y 10 años si los datos lo permiten.\n- Evolución de recompra de acciones y reducción neta real de acciones en circulación.\n- SBC como % de FCF operativo o FCF.\n- Señales de calidad contable y eventos de governance relevantes.\n\nTareas:\n1) Evalúa si el capital incremental genera retornos atractivos y consistentes (ROIIC).\n2) Realiza una AUDITORÍA FORENSE DE RECOMPRAS Y SBC: Determina si las recompras son creadoras de valor real (compras a múltiplos atractivos por debajo del valor intrínseco y que reducen de verdad la base de acciones) o destructivas (compras sobrevaloradas o usadas meramente para neutralizar la dilución masiva de los directivos). Revisa si la compensación basada en acciones (SBC) es abusiva o si diluye constantemente al accionista.\n3) Revisa goodwill, impairments, adquisiciones dudosas (M&A destructivo), sanciones, litigios o señales de sobrepago de adquisiciones.\n4) REGLA DE EVALUACIÓN DE ROIIC RECIENTE (CRÍTICA - PUNTO MEDIO): Si el ROIIC a 10 años es moderado/bajo (<12%) pero el ROIIC a 3 y 5 años demuestra una aceleración drástica y retornos excelentes (>15-20%), NO califiques con un suspenso ni con un 0.0 automático, pero TAMPOCO des la máxima puntuación de 2.0. Limita la nota de este pilar a un rango de 1.0 a 1.5 puntos para reflejar que la disciplina reciente es excelente, pero la directiva aún debe demostrar la sostenibilidad a largo plazo.\n\nCriterio de puntuación:\n- 2.0: asignación de capital altamente disciplinada y alineada a largo plazo (ROIIC medio a 10 años >15%), con recompras generadoras de valor real que reducen el recuento de acciones y SBC controlada.\n- 1.0 a 1.5: asignación aceptable o con clara trayectoria de mejora reciente excelente (ROIIC a 3-5 años >15%), o recompras neutrales, o SBC moderada pero con ciertos rezagos del pasado o bajo ROIIC a 10 años.\n- 0.0: mala asignación (M&A destructivo), dilución destructiva constante, recompras ineficientes para maquillar el SBC, o señales serias de mala gobernanza.\n\nSalida requerida:\n- Tabla resumen final con los 5 pilares (con la puntuación de cada uno).\n- Total sobre 10.\n- Veredicto final en mayúsculas y negrita.\n- Análisis forense de recompras vs SBC y gobernanza."
        }
    ]
}


def get_settings_from_db(db: Session) -> dict:
    db_settings = {row.key: row.value for row in db.query(DBSetting).all()}
    if not db_settings:
        return DEFAULT_SETTINGS
    # Combinar los valores por defecto con los de la base de datos para asegurar compatibilidad
    merged = DEFAULT_SETTINGS.copy()
    merged.update(db_settings)
    
    # Paso de migración/seguridad: si custom_evaluation_rules es un string en la DB o es la lista antigua de 11 normas, migrarlo
    custom_rules = merged.get("custom_evaluation_rules")
    if isinstance(custom_rules, str):
        if "Norma 1:" in custom_rules or "Norma 2:" in custom_rules or "Norma 3:" in custom_rules:
            # Es el string por defecto anterior; lo migramos automáticamente al nuevo array de las 5 normas
            merged["custom_evaluation_rules"] = DEFAULT_SETTINGS["custom_evaluation_rules"]
        else:
            merged["custom_evaluation_rules"] = [
                {
                    "title": "Mis Normas de Calidad (Migradas)",
                    "content": custom_rules
                }
            ]
    elif isinstance(custom_rules, list):
        # Migración automática si contiene las 11 normas antiguas o las 6 iniciales (con Paso 0)
        is_legacy = False
        if len(custom_rules) == 11 or len(custom_rules) == 6:
            is_legacy = True
        elif len(custom_rules) > 0 and isinstance(custom_rules[0], dict):
            first_title = custom_rules[0].get("title", "")
            if "Norma 1:" in first_title or "Moat & Bypass Test" in first_title or "Paso 0:" in first_title:
                is_legacy = True
        
        if is_legacy:
            merged["custom_evaluation_rules"] = DEFAULT_SETTINGS["custom_evaluation_rules"]
            
    return merged


def get_asset_repository(db: Session = Depends(get_db)) -> SqlAlchemyAssetRepository:
    return SqlAlchemyAssetRepository(db)


def get_portfolio_service(db: Session = Depends(get_db)) -> PortfolioService:
    return PortfolioService(SqlAlchemyPortfolioRepository(db))


def get_performance_service(
    portfolio_service: PortfolioService = Depends(get_portfolio_service),
) -> PerformanceService:
    return PerformanceService(portfolio_service)


def get_thesis_repository(db: Session = Depends(get_db)) -> SqlAlchemyThesisRepository:
    return SqlAlchemyThesisRepository(db)
