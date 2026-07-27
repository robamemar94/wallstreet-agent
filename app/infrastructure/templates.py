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


templates.env.globals["format_euro"] = format_euro
templates.env.globals["clean_ticker"] = clean_ticker
