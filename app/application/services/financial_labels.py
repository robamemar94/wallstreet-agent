"""
Las cuentas anuales de la ficha (Finanzas → Cuentas anuales) en español y con el formato de cada fila.

Las filas vienen en inglés y con nombres distintos según la fuente (TIKR, S&P, la IA…): aquí se traducen (lo que no
esté en la lista sale tal cual), se dice qué tipo de cifra es (importe, %, por acción o número de acciones) y si es un
total (en negrita) o una de las cifras clave (resaltada).
"""
import re
from typing import Any, Dict, List, Optional

ES = {
    # ── Cuenta de resultados ──
    "total revenues": "Ventas", "operating revenues": "Ventas de explotación", "total revenues %chg": "Ventas, variación",
    "total revenues % chg": "Ventas, variación", "other revenues": "Otros ingresos", "other revenues, total": "Otros ingresos",
    "total other revenues": "Otros ingresos", "transaction-based revenues": "Ingresos por transacciones",
    "property revenue": "Ingresos por inmuebles", "service and other revenue": "Servicios y otros ingresos",
    "net premiums earned": "Primas netas devengadas", "investment income": "Ingresos de inversiones",
    "total revenues before provision for credit losses": "Ingresos antes de provisiones",
    "total revenues before provision for credit losses %chg": "Ingresos antes de provisiones, variación",
    "total revenues after provision for credit losses": "Ingresos tras provisiones",
    "net interest income": "Margen de intereses", "non-interest income": "Ingresos sin intereses",
    "cost of sales": "Coste de ventas", "cost of goods sold, total": "Coste de ventas", "gross profit": "Beneficio bruto",
    "gross profit margin": "Margen bruto",
    "selling, general & administrative expenses": "Gastos generales y de venta",
    "selling general & admin expenses, total": "Gastos generales y de venta",
    "research & development expenses": "I+D", "r&d expenses": "I+D",
    "depreciation & amortization expenses": "Amortizaciones", "depreciation & amortization": "Amortizaciones",
    "amortization of goodwill and intangible assets": "Amortización de fondo de comercio e intangibles",
    "stock-based compensation": "Pagos en acciones", "other operating expenses": "Otros gastos de explotación",
    "other operating expenses, total": "Otros gastos de explotación", "provision for bad debts": "Provisión por impagos",
    "fuel and purchased power expense": "Combustible y energía comprada",
    "operations and maintenance expenses": "Operación y mantenimiento", "taxes other than income tax": "Otros impuestos",
    "property expenses": "Gastos de inmuebles", "total property expenses": "Gastos de inmuebles", "property taxes": "Impuestos sobre inmuebles",
    "insurance benefits & claims": "Siniestros y prestaciones", "other non-interest expenses": "Otros gastos sin intereses",
    "total non-interest expense": "Gastos sin intereses",
    "operating profit": "Beneficio operativo", "operating income": "Beneficio operativo", "operating margin": "Margen operativo",
    "ebitda": "EBITDA", "interest expense": "Gastos financieros", "interest expense, total": "Gastos financieros",
    "interest and investment income": "Ingresos financieros", "interest and invest. income": "Ingresos financieros",
    "net interest expenses": "Gasto financiero neto", "non-operating income": "Resultado no operativo",
    "total non-operating income": "Resultado no operativo", "other non operating income (expenses)": "Otros resultados no operativos",
    "income (loss) on equity invest": "Resultado de participadas", "currency exchange gains (loss)": "Diferencias de cambio",
    "gain (loss) on sale of assets": "Resultado por venta de activos", "gain (loss) on sale of investments": "Resultado por venta de inversiones",
    "net gains on disposal of operating assets": "Resultado por venta de activos", "net gains on disposal of properties": "Resultado por venta de inmuebles",
    "asset writedown": "Deterioro de activos", "impairment of goodwill": "Deterioro del fondo de comercio",
    "merger & related restructuring charges": "Costes de fusiones y reestructuración", "restructuring charges": "Reestructuración",
    "legal settlements": "Acuerdos judiciales", "insurance settlements": "Indemnizaciones de seguros", "other unusual items": "Otros extraordinarios",
    "ebt, excl. unusual items": "Beneficio antes de impuestos sin extraordinarios", "ebt, incl. unusual items": "Beneficio antes de impuestos",
    "income before provision for income taxes": "Beneficio antes de impuestos",
    "provision for income taxes": "Impuesto sobre beneficios", "income tax expense": "Impuesto sobre beneficios",
    "effective tax rate": "Tipo impositivo efectivo", "earnings from continuing operations": "Beneficio de actividades continuadas",
    "earnings of discontinued operations": "Beneficio de actividades interrumpidas",
    "net income attributable to discontinued operations": "Beneficio de actividades interrumpidas",
    "consolidated net income": "Beneficio neto consolidado", "net income": "Beneficio neto",
    "net income attributable to minority interests and other": "Atribuible a minoritarios", "minority interest": "Minoritarios",
    "net income attributable to preferred dividends": "Dividendos de preferentes",
    "net income attributable to common shareholders": "Beneficio neto atribuible",
    "net income to common incl extra items": "Beneficio neto atribuible (con extraordinarios)",
    "net income to common excl. extra items": "Beneficio neto atribuible",
    "basic eps": "BPA básico", "diluted eps": "BPA diluido", "eps": "BPA básico", "eps diluted": "BPA diluido",
    "basic weighted average shares outstanding": "Acciones medias", "weighted avg. shares outstanding": "Acciones medias",
    "diluted weighted average shares outstanding": "Acciones medias diluidas", "weighted avg. shares outstanding dil": "Acciones medias diluidas",
    "shares outstanding": "Acciones en circulación", "total shares outstanding": "Acciones en circulación",
    # ── Balance ──
    "cash and cash equivalents": "Efectivo", "cash and equivalents": "Efectivo", "short-term investments": "Inversiones a corto plazo",
    "short term investments": "Inversiones a corto plazo", "total cash and cash equivalents": "Caja total",
    "total cash and short term investments": "Caja total", "restricted cash": "Efectivo restringido",
    "restricted cash and segregated assets": "Efectivo restringido", "trading asset securities, total": "Valores de negociación",
    "trading assets": "Activos de negociación", "accounts receivable": "Clientes", "accounts receivable, total": "Clientes",
    "total trade receivables": "Clientes", "other receivables": "Otras cuentas a cobrar", "total receivables": "Cuentas a cobrar",
    "notes receivable": "Efectos a cobrar", "accrued interest and accounts receivable": "Intereses y cuentas a cobrar",
    "inventories": "Existencias", "inventory": "Existencias", "prepaid expenses": "Gastos anticipados",
    "deferred tax assets current": "Impuestos diferidos (corto)", "other current assets": "Otros activos corrientes",
    "other current assets, total": "Otros activos corrientes", "short-term regulatory assets": "Activos regulados a corto",
    "total current assets": "Activo corriente", "gross property plant and equipment": "Inmovilizado material bruto",
    "accumulated depreciation": "Amortización acumulada", "net property, plant & equipment": "Inmovilizado material",
    "net property plant and equipment": "Inmovilizado material", "goodwill": "Fondo de comercio",
    "net intangible assets": "Intangibles", "other intangibles, total": "Intangibles", "long-term investments": "Inversiones a largo plazo",
    "other investments": "Otras inversiones", "total investments": "Inversiones", "securities and investments": "Valores e inversiones",
    "loans receivable long-term": "Préstamos concedidos a largo", "accounts receivable long-term": "Cuentas a cobrar a largo",
    "deferred tax assets long-term": "Impuestos diferidos (largo)", "deferred charges long-term": "Gastos diferidos a largo",
    "long-term regulatory assets": "Activos regulados a largo", "special use funds": "Fondos de uso específico",
    "other long-term assets": "Otros activos no corrientes", "other long-term assets, total": "Otros activos no corrientes",
    "other non-earning assets": "Otros activos sin rendimiento", "other assets": "Otros activos",
    "securities borrowed": "Valores tomados en préstamo", "total assets": "Activo total",
    "accounts payable": "Proveedores", "accounts payable, total": "Proveedores", "accrued expenses": "Gastos devengados",
    "accrued expenses, total": "Gastos devengados", "short-term debt": "Deuda a corto plazo", "short-term borrowings": "Deuda a corto plazo",
    "current portion of long-term debt": "Deuda a largo con vencimiento a corto", "current portion of leases": "Arrendamientos a corto",
    "current income taxes payable": "Impuestos a pagar", "unearned revenue": "Ingresos anticipados",
    "unearned revenue current, total": "Ingresos anticipados", "unearned revenue non current": "Ingresos anticipados a largo",
    "unearned premiums": "Primas no devengadas", "claims reserves": "Provisiones técnicas", "trading liabilities": "Pasivos de negociación",
    "securities loaned": "Valores prestados", "short-term regulatory liabilities": "Pasivos regulados a corto",
    "other current liabilities": "Otros pasivos corrientes", "total current liabilities": "Pasivo corriente",
    "long-term debt": "Deuda a largo plazo", "total debt": "Deuda total", "leases": "Arrendamientos", "long-term leases": "Arrendamientos",
    "pension & other post retirement benefits": "Pensiones", "deferred tax liability non current": "Impuestos diferidos (pasivo)",
    "long-term regulatory liabilities": "Pasivos regulados a largo", "other long-term liabilities": "Otros pasivos no corrientes",
    "other non current liabilities": "Otros pasivos no corrientes", "other liabilities": "Otros pasivos",
    "total long-term liabilities": "Pasivo no corriente", "total liabilities": "Pasivo total", "preferred stock": "Acciones preferentes",
    "common stock": "Capital social", "common stock, total": "Capital social", "additional paid-in capital": "Prima de emisión",
    "additional paid in capital": "Prima de emisión", "retained earnings": "Reservas (beneficios retenidos)", "treasury stock": "Autocartera",
    "accumulated other comprehensive income": "Otro resultado global", "comprehensive income and other": "Otro resultado global",
    "total common shareholders' equity": "Patrimonio de los accionistas", "total common equity": "Patrimonio de los accionistas",
    "minority interests and other": "Minoritarios", "minority interest (equity)": "Minoritarios",
    "total shareholders' equity": "Patrimonio neto", "total equity": "Patrimonio neto",
    "total liabilities and shareholders' equity": "Pasivo y patrimonio neto", "total liabilities and equity": "Pasivo y patrimonio neto",
    # ── Flujos de caja ──
    "depreciation & amortization, total": "Amortizaciones", "amortization of deferred charges, total": "Amortización de gastos diferidos",
    "share-based compensation expense": "Pagos en acciones", "minority interest in earnings": "Resultado de minoritarios",
    "asset writedown & restructuring costs": "Deterioros y reestructuración", "(gain) loss from sale of asset": "(Beneficio) pérdida por venta de activos",
    "(gain) loss on sale of investments": "(Beneficio) pérdida por venta de inversiones", "(income) loss on equity investments": "Resultado de participadas",
    "provision and write-off of bad debts": "Provisión por impagos", "provision for credit losses": "Provisión por insolvencias",
    "tax benefit from stock options": "Ahorro fiscal por opciones", "change in deferred taxes": "Variación de impuestos diferidos",
    "other adjustments": "Otros ajustes", "other operating activities, total": "Otras partidas de explotación",
    "changes in trade receivables": "Variación de clientes", "change in accounts receivable": "Variación de clientes",
    "changes in receivables": "Variación de cuentas a cobrar", "changes in inventories": "Variación de existencias",
    "change in inventories": "Variación de existencias", "changes in accounts payable": "Variación de proveedores",
    "change in accounts payable": "Variación de proveedores", "changes in accrued expenses": "Variación de gastos devengados",
    "changes in unearned revenue": "Variación de ingresos anticipados", "change in unearned revenues": "Variación de ingresos anticipados",
    "changes in income taxes payable": "Variación de impuestos a pagar", "change in income taxes": "Variación de impuestos",
    "changes in other operating activities": "Otras variaciones de circulante", "change in other net operating assets": "Otras variaciones de circulante",
    "changes in unearned premiums": "Variación de primas no devengadas", "changes in claims reserves": "Variación de provisiones técnicas",
    "changes in restricted cash and segregated assets": "Variación de efectivo restringido", "changes in securities borrowed": "Variación de valores tomados",
    "changes in trading assets": "Variación de activos de negociación", "changes in trading liabilities": "Variación de pasivos de negociación",
    "change in trading asset securities": "Variación de valores de negociación", "changes in securities loaned": "Variación de valores prestados",
    "changes in accrued interest and accounts receivable": "Variación de intereses a cobrar",
    "miscellaneous cash flow adjustments": "Otros ajustes", "net cash from discontinued operations": "Caja de actividades interrumpidas",
    "cash from operating activities": "Flujo de caja operativo", "cash from operations": "Flujo de caja operativo",
    "capital expenditure": "Inversión en activos (capex)", "proceeds from sale of property, plant & equipment": "Venta de inmovilizado",
    "sale of property, plant, and equipment": "Venta de inmovilizado", "purchases of intangible assets": "Compra de intangibles",
    "proceeds from sale of intangible assets": "Venta de intangibles", "sale (purchase) of intangible assets": "Compra / venta de intangibles",
    "purchases of investments": "Compra de inversiones", "proceeds from sale of investments": "Venta de inversiones",
    "investment in marketable and equity securities, total": "Inversión en valores", "net change in securities and investments": "Variación de inversiones",
    "payments for business acquisitions": "Adquisiciones", "cash acquisitions": "Adquisiciones",
    "proceeds from business divestments": "Desinversiones", "divestitures": "Desinversiones",
    "sale (purchase) of real estate properties": "Compra / venta de inmuebles",
    "net (increase) decrease in loans originated / sold - investing": "Variación de préstamos concedidos",
    "other investing activities": "Otras inversiones", "other investing activities, total": "Otras inversiones",
    "cash from investing activities": "Flujo de caja de inversión", "cash from investing": "Flujo de caja de inversión",
    "issuance of long-term debt": "Emisión de deuda a largo", "long-term debt issued, total": "Emisión de deuda a largo",
    "repayments of long-term debt": "Devolución de deuda a largo", "long-term debt repaid, total": "Devolución de deuda a largo",
    "net issuance / (repayments) of long-term debt": "Deuda a largo: emisión neta",
    "issuance of short-term debt": "Emisión de deuda a corto", "short term debt issued, total": "Emisión de deuda a corto",
    "repayments of short-term debt": "Devolución de deuda a corto", "short term debt repaid, total": "Devolución de deuda a corto",
    "net issuance / (repayments) of short-term debt": "Deuda a corto: emisión neta", "total debt issued": "Deuda emitida",
    "total debt repaid": "Deuda devuelta", "issuance of common shares": "Emisión de acciones", "issuance of common stock": "Emisión de acciones",
    "repurchases of common shares": "Recompra de acciones", "repurchase of common stock": "Recompra de acciones",
    "net issuance / (repurchases) of common shares": "Acciones: emisión neta (− recompra)",
    "issuance of preferred shares": "Emisión de preferentes", "repurchases of preferred shares": "Recompra de preferentes",
    "net issuance / (repurchases) of preferred shares": "Preferentes: emisión neta",
    "common share dividends paid": "Dividendos pagados", "common dividends paid": "Dividendos pagados",
    "common & preferred stock dividends paid": "Dividendos pagados (con preferentes)", "preferred share dividends paid": "Dividendos de preferentes",
    "special dividend paid": "Dividendo extraordinario", "other financing activities": "Otras partidas de financiación",
    "other financing activities, total": "Otras partidas de financiación",
    "cash from financing activities": "Flujo de caja de financiación", "cash from financing": "Flujo de caja de financiación",
    "foreign exchange rate adjustments": "Efecto del tipo de cambio", "net change in cash": "Variación de caja",
    "free cash flow": "Flujo de caja libre", "levered free cash flow": "Flujo de caja libre apalancado",
    "unlevered free cash flow": "Flujo de caja libre sin deuda", "nopat": "NOPAT (beneficio operativo tras impuestos)",
}

TOTALS = {"Ventas", "Beneficio bruto", "Beneficio operativo", "EBITDA", "Beneficio antes de impuestos", "Beneficio neto consolidado",
          "Beneficio neto", "Beneficio neto atribuible", "BPA diluido", "Activo corriente", "Activo total", "Pasivo corriente",
          "Pasivo no corriente", "Pasivo total", "Patrimonio neto", "Patrimonio de los accionistas", "Pasivo y patrimonio neto",
          "Caja total", "Flujo de caja operativo", "Flujo de caja de inversión", "Flujo de caja de financiación", "Flujo de caja libre",
          "Margen de intereses", "Ingresos antes de provisiones", "Ingresos tras provisiones"}
KEY = {"Ventas", "Beneficio neto atribuible", "BPA diluido", "Activo total", "Patrimonio neto", "Flujo de caja libre", "Flujo de caja operativo"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower()).rstrip(".")


def label(name: str) -> str:
    return ES.get(_norm(name), (name or "").strip())


def kind(name: str) -> str:
    """pct (márgenes, variaciones, tipo impositivo) · per_share (BPA) · shares (número de acciones) · money (importes)."""
    n = _norm(name)
    if "margin" in n or "%chg" in n or "% chg" in n or "tax rate" in n:
        return "pct"
    if re.search(r"\beps\b", n) or "per share" in n:
        return "per_share"
    if "shares outstanding" in n or "shares outstanding dil" in n:
        return "shares"
    return "money"


def _scaled(v: Any, k: str) -> Optional[float]:
    if not isinstance(v, (int, float)) or v != v:
        return None
    v = float(v)
    if k == "pct":
        return v * 100 if abs(v) <= 1.5 else v
    if k in ("money", "shares"):
        return v / 1e6
    return v


def rows(section: Optional[Dict[str, List[Any]]], years: List[str]) -> List[Dict[str, Any]]:
    """Filas de una cuenta, de la más antigua a la más reciente (el LTM al final), en millones (los importes y las
    acciones), en % o por acción. Las filas vacías (sin ningún dato) no salen."""
    order = sorted(range(len(years)), key=lambda i: (str(years[i]).upper() == "LTM", str(years[i])))
    out = []
    for name, vals in (section or {}).items():
        k = kind(name)
        vals = list(vals or [])
        cells = [_scaled(vals[i], k) if i < len(vals) else None for i in order]
        if all(c is None for c in cells):
            continue
        lab = label(name)
        out.append({"name": name, "label": lab, "kind": k, "values": cells, "raw": [vals[i] if i < len(vals) else None for i in order],
                    "total": k != "pct" and (lab in TOTALS or _norm(name).startswith("total ")), "key": lab in KEY})
    return out


def ordered_years(years: List[str]) -> List[str]:
    return sorted((str(y) for y in years), key=lambda y: (y.upper() == "LTM", y))
