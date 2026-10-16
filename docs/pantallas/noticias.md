# Noticias (radar) y Calendario

## Noticias
`/noticias?ticker=…&semana=…`. Vista en `thesis_views.py`, plantilla `news.html`; servicios `thesis_news_runner` y
`news_summary_service`.

- **UNA semana cada vez:** `?semana=` es el sábado del radar. De serie, la última; también «Semana anterior» y
  «Elegir semana».
- **El filtro** solo afecta a la lista de noticias.
- **Al lado, siempre el resumen semanal:** el de la última semana, que es el único que se guarda.
- **Reparto del ancho:** lista 5/12 y resumen 7/12, este en dos columnas para que quepa sin scroll. Los dos bloques
  tienen el mismo alto (`data-fit-fill` y el script de `news.html`).
- **Accesos:** el icono junto a «Inicio» y «Noticias →» en Mi Cartera.

## Calendario
Ventana sobre Mercado, en `base.html`. Datos de `/api/calendario` (`calendar_views.py`, `results_calendar`).

- **Lo que viene:** los próximos eventos.
- **Lo ya ocurrido del último año**, apagado y tachado (`past`, leyenda «Ya ocurrido»; `past_events`):
  - resultados presentados, con la sorpresa del BPA (de la caché de resultados, sin pedir nada);
  - dividendos cobrados (del libro de operaciones).
