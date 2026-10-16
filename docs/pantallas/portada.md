# Portada (Mercado + Centro de mando)

Una sola vista, sin pestañas. `/mercado` y `/` (`/centro` lleva aquí).

## Qué se ve
- Arriba, la cinta de índices.
- La parte de arriba ocupa el 40 % del alto (`sizeHome`), con tres bloques:
  - **Mi Cartera:** valor; resultado de Hoy / 7 / 30 días frente al índice, con su propio selector
    (`/api/centro/pulso` → `periods`); avisos, resultados y cobro; «N noticias esta semana», que abre una ventana centrada
    `#newsModal` con `/api/noticias/semana`.
  - **Bandeja de hoy:** solo lo de hoy (también las señales de precio de hoy que ya no se cumplen), por grupos y con enlaces
    a Avisos. Su ✕ solo la quita de la bandeja de hoy, por grupo y empresa, guardado en la BBDD (`settings["bandeja_hidden"]`,
    `/api/portada/bandeja/quitar`).
  - **Agenda** como plan, en pestañas sin scroll:
    - «Esta semana»: día a día. Lo ya pasado desde el lunes (quién presentó, con la sorpresa del BPA, y qué se cobró) va
      resumido y apagado junto al título de cada bloque (`agenda_service.week_done`).
    - «30 días»: una línea por semana («Esta semana», «La próxima», «En 2 semanas»…, con las fechas al pasar el ratón;
      `agenda_weeks`), con «+N más» que despliega todas al pasar el ratón.
    - Tu cartera y lo que sigues, por separado.
- Abajo, la **Actividad del día**, que llena el resto.

## Cómo se carga
Vistas en `portada_views.py`. Cada bloque por su cuenta:
- `/portada/bandeja`: avisos ya guardados (~0,3 s).
- `/portada/agenda`.
- `/api/centro/pulso`: Mi Cartera.
- `/mercado/centro`: sincroniza los avisos en segundo plano; al terminar se refresca la Bandeja. También lo pide Avisos.

## Actividad del día
- Los valores cuyo mercado aún no ha abierto van ocultos en toda la sección (Ganadores, Perdedores,
  Favoritos/Compra/listas y Mi Cartera).
- El botón del ojo «🕒 N sin abrir» los enseña (`?ayer=1`). Entonces salen:
  - con su última sesión, atenuados (`tr[data-not-open]`);
  - con la etiqueta «ayer»/«vie» (`session_label`);
  - siempre debajo de lo de hoy.
- El mapa de calor y las cifras del día de la cartera siguen siendo solo de hoy.
- Pre/post-market: el dato sale al pasar el ratón por toda la fila (`ext_title`), en un globo verde o rojo
  (`data-ext-tip`). Ver `templates/CLAUDE.md`.
- Filtro por niveles (T1, T2, seguimiento, dudas…: el `status` de cada fila es 'T1'/'T2' o el estado); la tarjeta de la
  derecha sigue con Favoritos (= Tier 1) · Compra. Los filtros van en la dirección.
