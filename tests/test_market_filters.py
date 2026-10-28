"""Filtro de «Actividad del día»: país de la empresa (con respaldo por sufijo) y sesión (América / Europa / Asia-Pacífico)."""
from app.interfaces.views.market_views import MARKET_SESSION, country_info, get_market_group


def test_country_from_company_country_first():
    assert country_info("TSM", "Taiwan") == ("TW", "Taiwán")       # cotiza en EE. UU., pero es taiwanesa
    assert country_info("MELI", "Uruguay") == ("UY", "Uruguay")
    assert country_info("GRMN", "Switzerland") == ("CH", "Suiza")


def test_country_falls_back_to_ticker_suffix_then_us():
    assert country_info("IAG.MC", "-") == ("ES", "España")
    assert country_info("IGLN.L", "") == ("GB", "Reino Unido")
    assert country_info("6861.TWO", None) == ("TW", "Taiwán")      # «.TWO» antes que «.T» (Japón)
    assert country_info("6861.T", None) == ("JP", "Japón")
    assert country_info("BABA", "-") == ("US", "Estados Unidos")   # sin sufijo ni país: EE. UU.
    assert country_info("XYZ.ZZ", "-")[0] == ""


def test_every_market_group_has_a_session():
    for t, c, session in [("AMD", "United States", "america"), ("CP", "Canada", "america"), ("ASML.AS", "Netherlands", "europa"),
                          ("DGE.L", "United Kingdom", "europa"), ("EVO.ST", "Sweden", "europa"), ("TEA.AX", "Australia", "asiapac"),
                          ("6861.T", "Japan", "asiapac"), ("XYZ.ZZ", "-", "otros")]:
        assert MARKET_SESSION[get_market_group(t, c)] == session, t
