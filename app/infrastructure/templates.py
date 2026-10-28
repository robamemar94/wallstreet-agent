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


def ext_quote(ticker):
    """Pre-market / post-market de un valor de EE. UU. que aún no ha abierto hoy (o None): para stale_pill(ext=…)."""
    from app.interfaces.views.market_views import ext_quote as quote
    return quote(ticker)


templates.env.globals["ext_quote"] = ext_quote
EXT_BIG_PCT = 1.5
templates.env.globals["EXT_BIG_PCT"] = EXT_BIG_PCT   # pre/post-market: desde ±1,5 % se enseña el valor, destacado (si no, solo el icono); en Actividad del día, también decide qué valores con pre-market se ven sin abrir su mercado


def ext_lead(m) -> bool:
    """Actividad del día: aún no ha abierto y su pre-market es grande → ese precio es la cifra principal de la fila.
    El post-market no: corrige una sesión ya cerrada, que sigue siendo la cifra principal."""
    ext = m.get("ext") or {}
    return bool(m.get("not_open") and ext.get("kind") == "pre" and ext.get("pct") and ext.get("price")
                and abs(ext["pct"]) >= EXT_BIG_PCT)


def mover_values(m) -> dict:
    """La cifra principal de una fila de la Actividad del día: el pre/post-market (ext_lead) o su sesión."""
    if ext_lead(m):
        ext = m["ext"]
        return {"price": ext["price"], "chg": ext["price"] - m["price"], "pct": ext["pct"]}
    return {"price": m.get("price"), "chg": m.get("change_abs") or 0, "pct": m.get("pct") or 0}


templates.env.globals["ext_lead"] = ext_lead
templates.env.globals["mover_values"] = mover_values


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


def _ready_to_accept_count():
    from app.application.services.tier_service import ready_to_accept_count
    from app.infrastructure.db.database import SessionLocal
    db = SessionLocal()
    try:
        return ready_to_accept_count(db)
    except Exception:
        return 0
    finally:
        db.close()


# empresas analizadas sin aceptar ni nivel: el número de «BBDD» en el menú
templates.env.globals["ready_to_accept_count"] = _ready_to_accept_count
