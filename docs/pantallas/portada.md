# Portada (Mercado + Centro de mando)

Una sola vista, sin pestañas. `/mercado` y `/` (`/centro` lleva aquí).

## Qué se ve
- Arriba, la cinta de índices.
- La parte de arriba ocupa el 36 % del alto (`sizeHome`; lo que no cabe en la Agenda va en «+N más»), con tres bloques:
  - **Mi Cartera:** valor con su minigráfica (valor por participación del Rendimiento, `portfolio_since_start`; recorta
    el periodo elegido; en «Hoy» no sale: no hay datos del día) y, justo debajo, el resultado de Hoy / 7 d / 30 d / YTD /
    1 A / Máx frente al índice, con su propio selector (`/api/centro/pulso` → `periods`; «Máx» = «Desde el inicio» de
    Cartera, del Rendimiento, con lo ganado en € sin contar aportaciones). Debajo, en gris, el contexto: plusvalía de
    las posiciones abiertas y liquidez del bróker (`portfolio_day()` → `invested`, `cash_service`) y, solo en «Hoy», la
    que más sube y la que más baja de las ya abiertas (`movers`), en cursiva con su color. Luego, avisos, resultados y cobro (solo dividendos a 30 días; si hay varios, desplegable con quién paga cada uno); «N noticias esta semana», que abre una ventana centrada
    `#newsModal` con `/api/noticias/semana`. En «Hoy», si hay pre/post-market de EE. UU., al lado va el «hoy» ya sumado con
    él, discreto y con su icono (`portfolio_day()` → `ext`, la misma cuenta que debajo del Valor actual de Cartera).
  - **Bandeja de hoy, resumida** (26/10/2026): dentro de cada grupo, una línea por subtipo (`bandeja_subs` /
    `bandeja_compact` en `portada_views.py`: en el día, en 5 sesiones, lejos del máximo, máximo o mínimo de 52 s, pasa a
    barata o a ganga…) con las empresas en fila y solo su cifra (%, CAGR), siempre en línea (también si hay una sola).
    Primero ▼ lo que baja y, en otra línea, ▲ lo que sube, cada lado de mayor a menor; sin destacar ninguna (ni cartera ni σ: el
    usuario lo prefiere tranquilo; lo suyo, con el filtro «Cartera»). Lo que ya no se cumple, al final de su lado y algo
    apagado. Las flechas y los «·» los pone el JS tras filtrar lo de hoy. La frase completa, en el globo y en el detalle;
    las acciones (abrir, leído, ✕), flotando al pasar el ratón.
  - **Bandeja de hoy:** el nombre de la empresa sale en línea junto a los códigos numéricos (Asia: `214150 CLASSYS Inc.`), en el globo de todas y arriba del detalle desplegado. Solo lo de hoy (también las señales de precio de hoy que ya no se cumplen), por grupos y con enlaces
    a Avisos. Pulsar una línea despliega el detalle y la marca leída; su → abre directamente lo que avisa (también la
    marca leída). Su ✕ solo la quita de la bandeja de hoy, por grupo y empresa, guardado en la BBDD (`settings["bandeja_hidden"]`,
    `/api/portada/bandeja/quitar`).
    - **«Hoy presentan»** (`results_today` en `portada_views.py`): arriba de la Bandeja, solo los días con resultados,
      un mensaje (no un aviso: ni leído ni ✕) con las empresas en cartera (negrita) y las que sigues (favoritas, en
      seguimiento, watchlist o con tesis), agrupadas por momento: antes de abrir, en sesión, tras el cierre (con la hora
      de Madrid si Yahoo la da fiable) y sin hora. Mismas fechas que la Agenda, solo de lo ya guardado (`market_cache.peek`):
      no descarga nada. Cada nombre lleva a su tesis o a su ficha. Al día siguiente no se repite: ya salta el aviso
      «Resultados publicados… sin revisar».
  - **Agenda** como plan, en pestañas sin scroll:
    - «Esta semana»: día a día. Lo ya pasado desde el lunes (quién presentó, con la sorpresa del BPA, y qué se cobró) va
      resumido y apagado junto al título de cada bloque (`agenda_service.week_done`).
    - «30 días»: una línea por semana («Esta semana», «La próxima», «En 2 semanas»…, con las fechas al pasar el ratón;
      `agenda_weeks`), con «+N más» que despliega todas al pasar el ratón.
    - Tu cartera y lo que sigues, por separado.
      - «Lo que sigues» = Tier 1, 2 y 3, watchlist y con tesis. Dentro de cada día (y en «30 días», las primeras de cada
        semana) va primero el Tier 1 (`rank` en `other_agenda`); Tier 2, Tier 3 y el resto, sin distinguir. Formato: tu
        cartera en negrita azul; Tier 1 en seminegrita; el resto, en gris.
      - Cada empresa lleva su globo (`tk_info`), corto: el nombre y la hora de Madrid (el momento ya lo dice el icono); en
        los cobros, el importe. La fila no lleva un globo con todo junto.
      - Si las empresas de un día no caben en su línea, las últimas se esconden y van en «+N». Al pasar el ratón o al
        pulsarlo (se cierra al pulsar fuera) abre un desplegable con el formato de la agenda: una fila por empresa con el
        icono de cuándo, el ticker con su formato, el nombre y la hora (o el importe) (`.ap-tk` con `data-kind`,
        `data-name` y `data-when` en `_portada_agenda.html`; `fitAgendaPlan`).
    - **Sin recortes ni scroll:** la tarjeta tiene alto fijo; si las filas no caben, `fitAgendaPlan` (`index.html`) esconde las últimas del bloque más largo y las recoge en una línea «+N días más» (o «semanas»), con el detalle al pasar el ratón. Se recalcula al cambiar de pestaña, al redimensionar y al cargar la agenda.
    - **Hora de los resultados:** junto a cada empresa, amanecer = antes de la apertura, luna = tras el cierre y reloj =
      en sesión (la hora de Sevilla, en el globo y en «Esta semana»). Dentro de un día, primero las de antes de abrir.
      Sale de yfinance (`release_time`) y solo para EE. UU. con fecha a ≤ 45 días; si no es fiable, no se enseña.
      Yahoo fija la hora con el horario (verano/invierno) del día en que se mira: si el cambio de hora de EE. UU. cae antes
      de la fecha, `release_time` la corrige (si no, un «tras el cierre» de las 16:00 salía 15:00 «en sesión»).
    - **Fechas que faltan:** `results_calendar` no llama a la IA por su cuenta. Si hay 3 o más (cartera y favoritas) sin
      fecha y con su ciclo trimestral ya cerca (75 días desde la última presentación), la cabecera enseña «N fechas»:
      una sola consulta a Gemini con búsqueda para todas (`POST /api/agenda/fechas`). Las no halladas no se repiten
      hasta 45 días después.
    - **Lo ya publicado de esas fechas** (`results_calendar.published`; al buscar la siguiente, la pasada se guarda en
      `past`): cuenta para el aviso «Resultados publicados sin revisar» igual que lo que registra Yahoo. Así salta también
      con las empresas que publican solo ventas (Hermès en abril y octubre), que Yahoo no apunta porque no traen BPA.
- Abajo, la **Actividad del día**, que llena el resto.

## Cómo se carga
Vistas en `portada_views.py`. Cada bloque por su cuenta:
- `/portada/bandeja`: avisos ya guardados (~0,3 s).
- `/portada/agenda`.
- `/api/centro/pulso`: Mi Cartera.
- `/mercado/centro`: sincroniza los avisos en segundo plano; al terminar se refresca la Bandeja. También lo pide Avisos.

## Actividad del día
- **Filtros por sesión y por país** (en la cabecera de la sección, antes del filtro de estados):
  - **Sesión:** `Todos · 🌎 América · 🌍 Europa · 🌏 Asia-Pacífico`, cada uno con cuántas empresas hay (únicas, con el filtro de
    estados puesto). América = EE. UU., Canadá, México y Latinoamérica; Europa = continental, Reino Unido y nórdicos;
    Asia-Pacífico = Asia, Australia y Nueva Zelanda (`MARKET_SESSION` en `market_views.py`; `data-session` en cada fila).
  - **País:** desplegable `País ▾` con multiselección, bandera, nombre y recuento, ordenado por tamaño. Al abrirlo, el foco
    está en un buscador de texto (sin tildes ni mayúsculas: «sui» → Suiza); Enter marca el primero de los que quedan. Es el **país de la
    empresa** (TSM es Taiwán aunque cotice en EE. UU.; si falta, el del sufijo del ticker y, sin sufijo, EE. UU.:
    `country_info`, `data-country`). Se rellena solo con los países que hay.
  - Los dos modos no se mezclan: elegir un país deja sin marcar los botones de sesión, y pulsar una sesión quita los países.
  - En la dirección: `?region=america|europa|asiapac` o `?pais=SE,FR`. Los valores antiguos (`?region=usa|uk|nordicos|oceania|asia`)
    se traducen a país o sesión.
- Los valores cuyo mercado aún no ha abierto van ocultos en toda la sección (Ganadores, Perdedores,
  Favoritos/Compra/listas y Mi Cartera).
- **Excepción: los que ya tienen un pre-market o post-market de ±1,5 % o más** (`tr[data-ext-big]`, EE. UU.;
  umbral `EXT_BIG_PCT` en `app/infrastructure/templates.py`, el mismo con el que el valor sale en negrita) se ven
  siempre, sin pulsar nada, atenuados y debajo de los que ya han abierto, con su etiqueta y el icono (amanecer =
  pre-market; luna con estrellas = post-market de su última sesión, el de madrugada antes de que empiece el pre-market).
  - **Pre-market:** su cifra principal es la del pre-market (precio y variación frente al último cierre), con el icono
    en vez de «ayer»; su última sesión va en el globo de la fila (`ext_lead`/`mover_values` en `templates.py`; macros
    `mover_*` en `index.html`).
  - **Post-market:** la cifra principal sigue siendo la sesión cerrada, con «ayer» y el post-market al lado. Con menos de ±1,5 % siguen ocultos. El contador del ojo solo cuenta los que siguen ocultos.
  - **Orden:** primero lo de hoy; debajo, los que tienen pre/post-market grande, por el tamaño de ese movimiento (no
    por lo que hicieron en su última sesión); al final, el resto de sesiones anteriores (`activity_key` y `ext_move` en
    `market_views.py`).
  - **Ganadores / Perdedores:** estos valores van a la lista según el **signo del pre/post-market**
    (`activity_move`), no el de su última sesión. Con un movimiento pequeño sigue mandando la última sesión.
- **Post-market de hoy** (tras el cierre de EE. UU.): las filas de hoy llevan, junto al ticker, el icono del
  post-market de su sesión de hoy y, desde ±1,5 %, el valor (`post_today`). Es un dato secundario: la fila no cambia de
  lista ni de orden, que siguen siendo los de la sesión. El dato completo, al pasar el ratón por la fila.
- El botón del ojo «🕒 N sin abrir» enseña el resto (`?ayer=1`). Entonces salen:
  - con su última sesión, atenuados (`tr[data-not-open]`);
  - con la etiqueta «ayer»/«vie» (`session_label`);
  - siempre debajo de lo de hoy.
- El mapa de calor y las cifras del día de la cartera siguen siendo solo de hoy.
- Pre/post-market: el dato sale al pasar el ratón por toda la fila (`ext_title`), en un globo verde o rojo
  (`data-ext-tip`). Ver `templates/CLAUDE.md`.
- Filtro por niveles (T1, T2, seguimiento, dudas…: el `status` de cada fila es 'T1'/'T2' o el estado); la tarjeta de la
  derecha sigue con Favoritos (= Tier 1) · Compra. Los filtros van en la dirección.
