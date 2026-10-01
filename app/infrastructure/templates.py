from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def clean_ticker(ticker: str) -> str:
    if not ticker or not isinstance(ticker, str):
        return ticker
    if "." in ticker:
        # Remove the last part after the dot (exchange suffix)
        return ticker.rsplit(".", 1)[0]
    return ticker


def format_euro(value, decimals=2):
    if value is None:
        return "-"
    try:
        s = f"{value:,.{decimals}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(value)


def num(value, decimals=2, sign=False):
    """Número en formato español (1.234,56); sign=True antepone «+» a los positivos. Sin dato: «–»."""
    from utils.number_format import es
    return es(value, decimals, sign)


templates.env.globals["format_euro"] = format_euro
templates.env.globals["num"] = num
templates.env.globals["clean_ticker"] = clean_ticker


def _zone_cuts():
    from app.application.services import base_valuation_service as bv
    return {"ganga": bv.TARGET_RETURN_PCT, "barata": bv.BARATA_PCT, "justo": bv.JUSTO_PCT}


# cortes de las zonas de la valoración base (ganga/barata/justo/cara) para colorear el CAGR
templates.env.globals["ZONE"] = _zone_cuts()


def _portfolio_roles():
    from app.application.services.portfolio_roles_service import load_roles
    return load_roles()


# roles de cartera (CORE, GROWTH…) para los selectores y etiquetas de rol: portfolio_roles()
templates.env.globals["portfolio_roles"] = _portfolio_roles
