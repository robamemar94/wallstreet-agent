# Watchlist

`/watchlist`. La tabla se carga aparte desde `/watchlist/tabla`.

## Tabla
- A todo lo ancho, en **una sola lista** ordenada por distancia a compra.
- **Columna «Distancia a compra»:**
  - la barra lleva el color de la zona de valoración;
  - el % lleva el color de su tramo: verde en zona, ámbar < 15 %, gris 15–30 %, rojo > 30 % (`NEAR_PCT`, `WATCH_PCT`).
- **Filtro por tramo:** botones de color (`?distancia=`). Incluye «Mi precio».
- **Resto de filtros** (nivel, lista, rol, por revisar, ocultar las de cartera): en la dirección; el nivel con botones y lo
  demás en el desplegable «Filtros».
- Al ordenar por otra columna, el tercer clic vuelve al orden de serie. En la columna Día, primero los abiertos.

## Columna «Mi precio»
- Un precio de compra puesto a mano, aparte de la valoración. En las de la cartera es el precio **para ampliar**.
- Se pone aquí, en la cabecera de la ficha y en Cartera → Seguimiento (columna «Ampliar»), con `editMyPrice`
  (`_base/js_helpers.html`).
- **Vista unificada:** el filtro «Mi precio» enseña todas las que tienen precio, también las de cartera que no están en
  la watchlist (`only_mine`: solo salen en ese filtro), con «Ampliar · peso %». De serie se ordena por lo que falta
  para tu precio.
- Se guarda en `asset.data['my_buy_price']` = {price, set_at}, con `/api/watchlist/{t}/mi-precio`, y se ajusta por splits.
- Historial en `asset.data['my_buy_price_log']` = [{date, price}] (None: lo quitaste): la IA del trimestral y el anual
  ve si llegó, si compraste y qué hizo la acción después (`report_service.waiting_entries`).
- Avisos:
  - `mi_precio` al alcanzarlo (grupo Comprar; en las de cartera, «Ampliar: …»);
  - `mi_precio_revisar` a los 90 días (`MY_PRICE_REVIEW_DAYS`). Se da por revisado guardándolo de nuevo, aunque sea el mismo.

## Paneles laterales
- **Avisos** (`watchlist_attention`): zona, cerca de compra (Tier 1), mi precio, corrección expirada, directivos y
  previsiones.
- **Monitor de precios:** también el Tier 2 y 3 que no está en la watchlist (`followed_monitor`).

## Otros
- «Repasar niveles»: ventana para pasar el Tier 1 y el Tier 2 de nivel.
- ⚠ junto al CAGR si los supuestos son optimistas (ver «Valoración» en `app/CLAUDE.md`).
