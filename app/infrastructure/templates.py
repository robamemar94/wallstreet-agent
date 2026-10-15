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


def session_label(last_date) -> str:
    """Nombre corto de la última sesión de un mercado que aún no ha abierto hoy («ayer», «vie», «10/10»)."""
    import datetime
    from app.interfaces.views.market_views import session_label as label
    if isinstance(last_date, str):
        try:
            last_date = datetime.date.fromisoformat(last_date[:10])
        except ValueError:
            return ""
    if isinstance(last_date, datetime.datetime):
        last_date = last_date.date()
    return label(last_date, datetime.date.today()) if last_date else ""


templates.env.globals["session_label"] = session_label


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


# País (como lo da yfinance, en inglés) → código ISO de 2 letras y nombre en español, para las columnas estrechas
COUNTRIES = {
    "United States": ("US", "Estados Unidos"), "Sweden": ("SE", "Suecia"), "France": ("FR", "Francia"),
    "United Kingdom": ("GB", "Reino Unido"), "Canada": ("CA", "Canadá"), "Switzerland": ("CH", "Suiza"),
    "Spain": ("ES", "España"), "Germany": ("DE", "Alemania"), "Australia": ("AU", "Australia"),
    "Netherlands": ("NL", "Países Bajos"), "Italy": ("IT", "Italia"), "Mexico": ("MX", "México"), "Japan": ("JP", "Japón"),
    "Poland": ("PL", "Polonia"), "Norway": ("NO", "Noruega"), "Uruguay": ("UY", "Uruguay"), "Taiwan": ("TW", "Taiwán"),
    "Singapore": ("SG", "Singapur"), "New Zealand": ("NZ", "Nueva Zelanda"), "Luxembourg": ("LU", "Luxemburgo"),
    "Ireland": ("IE", "Irlanda"), "Greece": ("GR", "Grecia"), "Finland": ("FI", "Finlandia"), "Denmark": ("DK", "Dinamarca"),
    "Cyprus": ("CY", "Chipre"), "China": ("CN", "China"), "Belgium": ("BE", "Bélgica"), "Austria": ("AT", "Austria"),
    "Portugal": ("PT", "Portugal"), "Israel": ("IL", "Israel"), "Brazil": ("BR", "Brasil"), "India": ("IN", "India"),
    "South Korea": ("KR", "Corea del Sur"), "Hong Kong": ("HK", "Hong Kong"), "Bermuda": ("BM", "Bermudas"),
    "Cayman Islands": ("KY", "Islas Caimán"), "Jersey": ("JE", "Jersey"), "Argentina": ("AR", "Argentina"),
    "Chile": ("CL", "Chile"), "Colombia": ("CO", "Colombia"), "Peru": ("PE", "Perú"), "South Africa": ("ZA", "Sudáfrica"),
}


def country_iso(name):
    """(código, nombre en español) de un país; si no está en la lista, las 2 primeras letras y el nombre tal cual."""
    if not name or name == "-":
        return ("–", "Sin país")
    return COUNTRIES.get(name, (name[:2].upper(), name))


templates.env.globals["country_iso"] = country_iso
