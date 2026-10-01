"""Números en formato español (1.234,56) para los textos que ve el usuario (avisos, señales, plantillas)."""


def es(value, decimals: int = 2, sign: bool = False) -> str:
    """es(1234.5) → '1.234,50'; sign=True antepone «+» a los positivos. Sin dato: «–»."""
    if value is None or value == "":
        return "–"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    s = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("+" if sign and value > 0 else "") + s
