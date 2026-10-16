# Avisos

`/avisos` (antes «Alertas»; `/alerts` lleva aquí). La lista se pide a `/avisos/lista`.

Vista: `alerts_views.py` (`notices_context`). Servicio: `notification_service` (`collect`, `filter_of`, `FILTERS`).

## Lista
Todas las notificaciones de la campana, en una lista por días.

## Grupos (a la izquierda)
- Siete grupos, cada uno con su recuento: Comprar, Directivos y analistas, Resultados, Precio y Tu cartera; y, ocultos de
  serie, Otros (cambios de zona que no van hacia compra, y recuperaciones) y Noticias.
- Pulsar uno enseña solo ese (`?tipo=`).
- «Todo» enseña todos menos los ocultos con el ojo; eso se recuerda en `localStorage`.

## Encima de la lista
- Sin leer / todos, cartera / seguimiento, y un buscador por código, nombre o texto (sin tildes). Todo va en la dirección.
- Un índice de días (Hoy · Ayer · Esta semana · Este mes · Antes) que salta a esa parte. No filtra.

## Cada aviso
- Las alertas llevan la fecha en que empezaron, no la de su última actualización.
- Cada aviso dice si sigue **vigente**: desde cuándo y, al pasar el ratón, hasta cuándo (`VIGENTE_RULE`).
- Selector Vigentes / Sin leer / Todo (de serie, Vigentes).
- En Comprar, Precio y Otros, una línea por empresa: la última, y «×N» despliega las anteriores (`group_by_company`).

## Al abrirse
Recalcula en segundo plano los avisos del Centro y de la Watchlist (sin IA).

## Tipo de aviso nuevo
Hay que darlo de alta en tres sitios:
- `KIND_LABEL`, `FILTER_OF_TYPE` y `VIGENTE_RULE` (`notification_service`);
- `NOTICE_TAG` (`_notices.html`).
