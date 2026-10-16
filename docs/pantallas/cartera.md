# Cartera

`/portfolio`. Se entra en Posiciones.

## Archivos
`portfolio.html` es solo el esqueleto: cabecera, menú de pestañas e includes. Las piezas están en `templates/_cartera/`:

| Pieza | Archivos |
|---|---|
| Estilos | `estilos.html`, `estilos_pagina.html` |
| Pestañas | `posiciones.html`, `distribucion.html`, `rendimiento.html`, `fundamentales.html`, `dividendos.html`, `resumen.html`, `historial.html` |
| Ventanas de liquidez y nueva operación | `ventanas.html` |
| JavaScript | `js/posiciones` (Contable/Seguimiento, mapa, distribución, cambio de vista), `js/decisiones_informes`, `js/liquidez_resumen`, `js/rendimiento`, `js/paginas_periodos`, `js/operaciones` (formulario y `sortTable`), `js/historial` |

Decisiones e Informes se cargan aparte (`_decisions.html`, `_reports.html`), y Seguimiento también (`_positions_tracking.html`).

## Pestañas
- Resumen (la primera del menú): `portfolio_overview`.
- Posiciones: Contable · Seguimiento · Fundamentales.
- Rendimiento: Gráfica · Informes.
- Distribución.
- Dividendos.
- Operaciones: Historial · Decisiones.

Detalles de navegación:
- Hashes que abren cada pestaña: `#fundamentales`, `#rendimiento`, `#informes`, `#historial`, `#decisiones`,
  `#resumen`, `#seguimiento`, `#mapa`…
- Fragmentos que se cargan aparte: `/portfolio/seguimiento`, `/portfolio/decisiones`, `/portfolio/informes`.
- El menú está agrupado con `data-pf-group`; las pestañas de Bootstrap de siempre siguen ocultas en `#portfolioTabs`.

## Posiciones → Contable y Seguimiento
- **Var. día € de mercado abierto:** con fondo suave (`eur-open`), para que destaque.
- **Var. día € de EE. UU. con horario extendido:** discreta (apagada, en cursiva y con su icono):
  - antes de abrir, lo del pre-market;
  - tras el cierre, la sesión corregida con el post-market, hasta que abre Australia (`asia_open_after`, 10:00 en Sídney).
  - Piezas: `ext_eur` (`_ui_macros.html`), `extEur` (JS), `ext_day` (`market_views.py`), `ext_day_eur` (`portfolio_views.py`).
  - En Seguimiento, solo con el periodo 1D (`live_day` en `thesis_views.py` pone `c.ext_day`).
- **Debajo del «hoy» de Valor actual:** el icono y el de hoy ya sumado con el horario extendido (el «hoy» sigue sin él).
- **Al ordenar por la variación del día:** primero los abiertos y luego los que no (`data-sort-group`).

## Otras piezas
- Seguimiento: columna «Prev. 90 d» (previsiones de los analistas) y «Máx. hist.».
- Liquidez (recuadro en Posiciones, ventana `#liquidez`, línea del Resumen): ver «Liquidez» en `app/CLAUDE.md`.
- Tarjetas 1S–1A con el efecto divisa (`fx_eur`).
- Decisiones e Informes: ver «Operaciones» e «Informes» en `app/CLAUDE.md`.
