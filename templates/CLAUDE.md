# Convenciones de interfaz (plantillas)

Se carga al trabajar en `templates/`. Las reglas generales (sin scroll, filtros en la dirección, números en español) están en
el `CLAUDE.md` de la raíz; los conceptos de dominio, en `app/CLAUDE.md`; el detalle de cada pantalla, en `docs/pantallas/`.

## `base.html` (partido en `_base/`)
`base.html` es el esqueleto: `<head>`, los `{% block %}` y los includes. Las piezas:
- **`_base/js_helpers.html`:** helpers JS comunes.
  - `fmtNum`, `urlState`, `chgPill`, `stalePill`, `extTag`, `extEur`, `sessionLabel`;
  - `rowsPerPage`/`pageOverflowRows`, `fitScroll`, `fitChartHeight`.
- **`_base/estilos.html`:** estilos comunes (píldoras, `ext-tag`, `ext-eur`, `eur-open`, globos de color…).
- **`_base/menu.html`:** la barra lateral. `_path` se define en `base.html`.
- **`_base/notificaciones.html`:** el panel de la campana.
- **`_base/js/avisos.html`:**
  - leído / no leído (`markNotificationsRead`), globos, campana y radar;
  - la cifra del día de la cartera (`/api/portfolio/day`, con `portfolio_day()`, la misma cuenta que la tarjeta
    «Mi Cartera»).
- **`_base/js/buscador_y_varios.html`:** buscador, modo oscuro, `showToast`, aviso de consumo de IA y pestañas.
  - Otro campo de empresa con el mismo buscador (nombre o ticker): `<input id="x" data-ticker-search="#xSug">` y su
    `<div id="xSug" class="search-suggestions">` dentro de un `.suggestions-container`. Elegir rellena el ticker sin
    enviar el formulario.
- **`_base/ventanas.html`:** las ventanas de noticias de la semana y del calendario.
- **En `base.html`:** el acceso «Inicio», la barra de búsqueda y el globo de color del pre/post-market (`[data-ext-tip]`).
- **Macros comunes:** en `_ui_macros.html` (`chg_pill`, `stale_pill`, `ext_tag`, `ext_title`, `ext_eur`) y
  `_notices.html`.

## Mercado aún sin abrir (formato estándar)
- **Qué se enseña:** la variación de la última sesión, con `stale_pill` (en JS, `stalePill`).
  - Mismo color que `chg_pill`, pero apagado, sin relleno, con borde discontinuo, luna y el día («ayer»/«vie»).
  - En columnas estrechas (Contable, Seguimiento, Base de datos), `day=false`.
- **No cuenta en las cifras de hoy** (€ del día, totales).
- **Al ordenar por la variación del día:** va después de los abiertos, ordenado por su última sesión
  (`data-sort-group="1"`). Así en Contable, Seguimiento, Watchlist, Listas y Base de datos (en Listas, con
  `data-not-open` en la fila).

## Pre-market / post-market (EE. UU.)
- **En tablas y portada:** lo de la última sesión sigue siendo lo principal. Al lado, solo un icono (amanecer =
  pre-market, luna con estrellas = post-market):
  - en las tablas va a la izquierda de la píldora, para que queden alineadas;
  - el dato sale al pasar el ratón, en un globo verde o rojo (`data-ext-tip`/`data-ext-tone`);
  - en la Actividad del día, el globo sale al pasar por toda la fila (`ext_title`);
  - si se mueve ±1,5 % o más (`EXT_BIG_PCT`, global de Jinja), además el valor en negrita;
  - excepción: en la Actividad del día, un valor que aún no ha abierto con un pre-market de ±1,5 % o más lo
    enseña como cifra principal (el post-market, no) (`ext_lead`); ver `docs/pantallas/portada.md`;
  - sin moverse (0,00 %), nada.
- **En la ficha:** siempre el dato completo junto al precio.
- **En Cartera:** Var. día € con el horario extendido; ver `docs/pantallas/cartera.md`.
- **De dónde sale:**
  - `refresh_extended_prices` usa `Ticker.history` con `prepost`, valor a valor. **No** `yf.download`, que no aguanta dos
    descargas a la vez.
  - Corre en segundo plano tras cada carga de precios, como mucho cada 4 min, y también pide el post-market del mismo día.
  - Lo guarda en `INDEX_CACHE[t]['ext']`. Se lee con `ext_quote(t)` (global de Jinja; solo si no ha abierto hoy) o con
    `ext_day(t)` (la cartera).
- **No cuenta en las cifras de hoy.**

## Otros formatos
- **Precios sin decimales** en monedas sin céntimos (yen, won…: `NO_CENTS`, `price_decimals()`): 81.230 ¥. Hecho en la
  Actividad del día, Watchlist y Listas (campo `dec`).
- **Filtros en la dirección:** hechos en Watchlist, Actividad del día y Base de datos (pestaña, búsqueda, filtros, nota,
  orden y periodo). Ojo: leer la dirección al cargar ANTES del primer filtrado (el arranque guarda su estado y la pisaría).

## Plantillas grandes partidas
- La ficha (`ticker.html`) está partida en `_ficha/`, la Cartera (`portfolio.html`) en `_cartera/` y `base.html` en `_base/`, con `{% include %}`.
- Un `{% set %}` dentro de un include no se ve fuera de él. Lo que comparten varias piezas va en la plantilla principal.
- Los `{% block %}` no pueden ir dentro de un include: se quedan en la plantilla principal.
- Para una macro que se usa en varias piezas: `{% from … import … with context %}`.
- Al partir otra plantilla, comprobar que el HTML que sale es el mismo: comparar con `diff -w -B` el de antes y el de
  después, renderizados uno detrás de otro.
