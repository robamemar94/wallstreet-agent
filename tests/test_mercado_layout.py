"""Portada en dos pestañas (Mercado y Centro de mando); Watchlist y Tesis y noticias van en el menú; el calendario se
abre desde arriba."""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_home_has_market_and_command_center_tabs():
    html = client.get("/").text
    assert 'data-bs-target="#home-mercado"' in html and 'data-bs-target="#home-centro"' in html
    for kept in ("heatmapTracked", "renderHeatmap", "centroPulse", "fetch('/api/centro/pulso')"):   # pestaña Mercado
        assert kept in html
    for slot in ("slot-summary", "slot-attention", "slot-agenda"):   # pestaña Centro: la bandeja (avisos, noticias, monitor) y la columna
        assert f'id="{slot}"' in html
    assert 'id="slot-news"' not in html                              # las noticias son una pestaña de la bandeja
    assert "fetch('/mercado/centro' + " in html
    for gone in ("weeklyCalendarList", "earnList", "divList"):   # ahora en la agenda y en el calendario
        assert gone not in html


def test_menu_has_thesis_and_watchlist_and_calendar_button():
    html = client.get("/mercado").text
    assert 'href="/tesis" title="Tesis y noticias"' in html and 'href="/watchlist"' in html
    assert 'href="/centro"' not in html and 'href="/mercado"' not in html   # a la portada se va con el logo
    assert 'class="sidebar-brand d-flex align-items-center justify-content-center mb-3 mt-2 active" href="/"' in html
    assert 'data-bs-target="#calModal"' in html and 'id="calModal"' in html


def test_old_pages_redirect():
    for old, new in (("/centro", "/mercado#centro"), ("/calendario", "/mercado#calendario"), ("/oportunidades", "/watchlist")):
        r = client.get(old, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == new


def test_watchlist_page_loads_its_table_apart():
    html = client.get("/watchlist").text
    assert "fetch('/watchlist/tabla')" in html and "function initNoticeLists" in html


def test_calendar_api_returns_events():
    d = client.get("/api/calendario").json()
    assert "today" in d and isinstance(d["events"], list)
    assert all(e["category"] in ("portfolio", "favorite", "seguimiento", "dividend") for e in d["events"])
