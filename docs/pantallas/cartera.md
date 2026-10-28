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
- Resumen (la primera del menú): `portfolio_overview`. Riesgo en dos columnas: concentración (lo que suman las 3 y las 5 mayores, con
  cuáles en el icono ⓘ; diversificación real, divisa y sector principales) y comportamiento desde el inicio del Rendimiento
  (`risk_since_start`: volatilidad, beta medida con la cartera, «por tu selección», caída máxima y desde el máximo).
- **Resumen → «Frente al índice»:** la curva ocupa todo el ancho de su tarjeta y el alto que queda en la ventana (`fitChartHeight`), con el navegador de rango (años/meses) debajo. Arriba, una tira de periodos (1 mes · Este año · 1 año · Desde el inicio: % de la cartera y pp frente al índice; ganancia, divisa e índice en el globo) que hace zoom al tramo; al mover el navegador se desmarca y el eje Y se ajusta a lo visible. La leyenda va en la cabecera.
- **Resumen → reparto:** arriba, una franja fina con 4 cifras (valor con el «hoy», ganancia neta total con su %, dividendos «cobrados en 12 m · previstos» (`dividends_12m` = lo cobrado en los últimos 365 días; los previstos son los de `dividend_forecast`), patrimonio con la liquidez; el detalle, en los globos). Debajo, a la izquierda, una columna con «Qué ha movido el último mes» (barras divergentes: 5 que más suman | 5 que más restan, con una línea de los informes semanal y mensual que lleva a Informes), «Próximos 30 días» (resultados y cobros en 2 líneas; detalle en el globo) y Riesgo. A la derecha (4/6), la gráfica y, bajo ella, «Calidad y precio» en baldosas (2 filas de 5) con los mismos umbrales de color que Fundamentales y el enlace «Desglose por empresa →». «Tus decisiones» ya no está en el Resumen (sigue en Operaciones → Decisiones).
- **Resumen → gráfica en %:** las dos curvas se rebasan a 0 % en el inicio del tramo visible (`rebase` en `js/liquidez_resumen.html`), así que al pulsar 1 mes, Este año… o mover el navegador de rango, el eje dice cuánto ha subido cada una desde ahí. La pastilla «Frente al índice» no está en la franja: se lee en «Desde el inicio».
- Posiciones: Contable · Seguimiento · Mapa. Son tres botones de la cabecera (`data-pos-view`, `showView` en `js/posiciones.html`); el mapa de calor (tamaño = peso, color = variación del periodo, y la contribución al lado) comparte datos y periodo con la tabla de Seguimiento y se abre con `#mapa`. La banda 1D · 1S · 1M · YTD · 1A elige el periodo de las dos.
- **Resumen → Patrimonio** (posición global): acciones + roboadvisor + liquidez. El roboadvisor lo suma
  `js/roboadvisor.html` al cargar (`/api/portfolio/roboadvisor`, «~» si es estimado) y lleva a su pantalla (`/roboadvisor`).
  Es información aparte: no entra en el valor, la rentabilidad ni los pesos de la cartera. Ver «Roboadvisor» en `app/CLAUDE.md`.
- Fundamentales (pestaña propia desde el 20/10/2026; antes un botón dentro de Posiciones).
- **Rendimiento:** arriba, una fila de baldosas (rótulo, cifra grande y detalle); gráfico y drawdown a la izquierda, tabla
  por año / por mes a la derecha.
  - Sin navegador de rango: el periodo se elige con los botones de «Histórico» (o arrastrando sobre el gráfico). Las fechas
    van bajo el gráfico de valor y el drawdown no las repite; los dos comparten margen izquierdo (`PERF_ML`) para que
    alineen. La leyenda va sobre el gráfico.
  - **€ Real:** como «Mi dinero» de la ficha: lo que vale la cartera, lo aportado en escalón (`contributed_eur` de
    `performance_service`: compras − ventas, en € del día) y el relleno entre ambos; el globo da la ganancia de ese día.
- Informes (antes dentro de Rendimiento).
- Distribución.
- Dividendos.
- Operaciones: Historial · Decisiones.
- **Nueva operación / editar** (`_cartera/ventanas.html`, `js/operaciones.html`): «Revisar y guardar» enseña un resumen
  (qué, cuánto, comisión, bróker, cambio; al editar, cómo era antes) y solo «Confirmar» guarda. Avisa en rojo si el precio se
  aleja más de un 20 % de la cotización, si la moneda no es la del valor, si vendes más acciones de las que tienes o si no
  hay cotización del ticker (`/api/portfolio/check/{t}`). Las operaciones añadidas a mano se editan desde el «⋯» de la
  ficha o el ✏️ de la página de la posición: llevan a Cartera con `?editar=TICKER|fecha|TIPO|acciones|precio`
  (`GET /api/portfolio/transaction/{t}`, `POST /api/portfolio/transaction/update/{t}`; el porqué y la foto de la
  valoración pasan a la operación editada).

Detalles de navegación:
- Hashes que abren cada pestaña: `#fundamentales`, `#rendimiento`, `#informes`, `#historial`, `#decisiones`,
  `#resumen`, `#seguimiento`, `#mapa`…
- Fragmentos que se cargan aparte: `/portfolio/seguimiento`, `/portfolio/decisiones`, `/portfolio/informes`.
- El menú está agrupado con `data-pf-group`; las pestañas de Bootstrap de siempre siguen ocultas en `#portfolioTabs`.

## Posiciones → Contable y Seguimiento
- **Contable** (11 columnas, rehecha el 25/10/2026), **una línea por celda** y lo secundario en el globo: Activo (ticker y
  nombre en gris) · Último · Hoy % · Hoy € · Títulos (globo: primera compra y antigüedad, `held_label`) · P. medio ·
  Invertido · Valor · Peso · P&L € (globo: acción/divisa y, si hay ventas parciales o dividendos, el total
  `total_result_eur`) · P&L % (globo: TIR `irr`, ponderada por el dinero, con un año o más).
  Todo cuenta desde que se abrió **la posición actual** (`holding_since`): si se cerró y se volvió a comprar (META, NVDA),
  lo anterior es otra inversión (antigüedad, ventas, dividendos y TIR empiezan de nuevo).
- **Columna TIR (pendiente, decidido el 25/10/2026):** no se pone hasta que la mayoría de posiciones pase del año (hacia
  mediados de 2027); hoy saldría «–» en 8 de 12. Ya se calcula (`item.irr`, `None` con menos de un año): basta con una
  columna «TIR» a la derecha de P&L % que **sustituya a Invertido** (Valor − P&L € da lo mismo), con el mismo reparto
  de anchos en `estilos.html`, y quitar la TIR del globo de P&L %. Hoy va partido en % (comparar empresas) y € (lo que te
  afecta), cada uno ordenable. Sin menú ⋯ (se borra operación a operación en la ficha de la posición). Anchos por
  posición en `estilos.html` (suman el 100 %).
- **Invertido = lo pagado en euros** (`total_cost_eur`, cambio de cada compra), no títulos × medio al cambio de hoy: el P&L
  y la «Rent. latente» incluyen el efecto divisa y cuadran con su globo (acción + divisa, `pnl_price_eur`/`pnl_fx_eur`).
  Una operación a mano sin cambio guardado (Yahoo no respondió al apuntarla) lo recibe en el siguiente recálculo
  (`fill_missing_fx`, `utils/fx.py`); solo si Yahoo sigue sin dato se usa uno fijo (0,92 USD) en `rebuild_portfolio`.
- **Var. día € de mercado abierto:** con fondo suave (`eur-open`), para que destaque.
- **Var. día € de EE. UU. con horario extendido:** discreta (apagada, en cursiva y con su icono):
  - antes de abrir, lo del pre-market;
  - tras el cierre, la sesión corregida con el post-market, hasta que abre Australia (`asia_open_after`, 10:00 en Sídney).
  - Piezas: `ext_eur` (`_ui_macros.html`), `extEur` (JS), `ext_day` (`market_views.py`), `ext_day_eur` (`portfolio_views.py`).
  - En Seguimiento, solo con el periodo 1D (`live_day` en `thesis_views.py` pone `c.ext_day`).
- **Debajo del «hoy» de Valor actual:** el icono y el de hoy ya sumado con el horario extendido (el «hoy» sigue sin él).
- **Al ordenar por la variación del día:** primero los abiertos y luego los que no (`data-sort-group`).

## Otras piezas
- Seguimiento: columna «PER fwd» (a la izquierda de «CAGR est.»: precio sobre el beneficio esperado a 12 meses, de la caché de 12 h de `portfolio_raw`; verde ≤ 20x, rojo > 35x, «–» si se esperan pérdidas; los mismos umbrales que Fundamentales). La tabla tiene 13 columnas (sin «Var. €»: el resultado en € del periodo, con divisa, va en el globo de la variación %) y sus anchos son **por posición** (`.tbl-pt th:nth-child(n)` en `estilos.html`, suman el 100 %): al añadir o quitar una columna hay que rehacer el reparto o alguna se queda a 0.
- Seguimiento: columnas «Prev. 90 d» (previsiones de los analistas), «Máx. hist.» y «Ampliar» (lo que falta para tu
  «Mi precio»; se edita ahí, ver `docs/pantallas/watchlist.md`).
- Liquidez (recuadro en Posiciones, ventana `#liquidez`, línea del Resumen): ver «Liquidez» en `app/CLAUDE.md`.
- Tarjetas 1S–1A con el efecto divisa (`fx_eur`).
- Decisiones: ver «Operaciones» en `app/CLAUDE.md`. Informes: `informes.md`.
