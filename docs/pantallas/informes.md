# Informes (Cartera → Informes)

Plantilla `_reports.html` (fragmento `/portfolio/informes`, hash `#informes`); servicio `report_service`. Resumen corto en `app/CLAUDE.md`.

- Fotos fijas semanales, mensuales, trimestrales y anuales.
- El semanal, desde el sábado a las 12:00 y cuando ha terminado el radar de esa semana (`thesis_news_runner.radar_pending`;
  al acabar, el radar lo pide). Sus noticias, las que encontró ese radar (`radar_window`): las de su resumen semanal.
- Se guardan en `settings` con la clave `report:<W|M|Q|Y><periodo>`.
- El mes, el trimestre y el año en curso (`live_report`, clave `M-en-curso`…): del primer día a hoy, se calculan al
  abrir Informes (caché de 30 min; se preparan al entrar en Cartera, `warm_live`), sin guardarse, sin aviso y sin IA.
  La semana en curso no.
- Se generan solos, SIN IA, al arrancar y cada hora. También hacia atrás desde `performance.start_date`, marcados
  «reconstruido».
- IA:
  - comentario corto en el mensual (Flash, con el botón);
  - revisión trimestral y carta anual (Pro, `GEMINI_PRO_MODEL`): se escriben solas en los informes nuevos (excepción a la
    regla) y con el botón en los reconstruidos.
- La carta anual guarda tus objetivos del año siguiente.
- Semanal y mensual: una pantalla en tres columnas (resultado · lo que hiciste · contexto). Compras y ventas por separado.
- Si el Rendimiento (`performance.start_date`) empieza dentro del periodo, el informe se estudia desde ese día
  (`study_start`, guardado en `since`): lo de antes era la cartera vieja. `backfill_sections` rehace los guardados sin él.
- Trimestral y anual: todas las secciones seguidas, con scroll solo dentro del informe (`stacked` en `_reports.html`;
  gráficas con `repCharts`). El usuario no quiere pestañas: lo quiere todo de un vistazo. Arriba, un índice con forma de
  pestañas (`data-rep-goto`) que baja a cada sección y marca en la que estás al hacer scroll.
  - Resumen: cifras con la peor caída (`drawdown`), la curva frente al S&P 500 (`curve`, del valor por participación del
    Rendimiento) y «Lo esencial» (`takeaways`, frases con los datos, sin IA); debajo, el riesgo (`risk`: rentabilidad sin
    aportaciones, volatilidad, beta y, en `display`, «por el riesgo asumido» = beta × S&P y «por tu selección» = el resto,
    sin tipo sin riesgo) y «Tu gestión»; a la derecha, a todo el alto, cómo se llegó al valor (`bridge`: una sola barra
    apilada —al empezar, aportaciones, empresas, divisa— con cada cifra a la altura de su tramo). «Tu gestión»: frente
    a no tocar la cartera del principio (`inaction`), momento de tus aportaciones (la tuya menos la de la cartera), coste
    de la liquidez (`cash_cost`, con 20 sesiones como mínimo) y empresas en positivo;
  - Rentabilidad: mes a mes, las 5 que más sumaron y restaron (también las vendidas), empresas frente a divisa, por
    sector (`sectors`) y ganancias (`gains`: plusvalía latente al cierre y realizada por venta, a precio medio en euros,
    sin comisiones);
  - Compras y ventas: por empresa con precio medio; al pasar el ratón (o tocar), sus órdenes en una ficha flotante; las que mejor y peor salieron
    (`decision_results`);
  - Cartera (y tesis): entradas, salidas, pesos y concentración (`portfolio_shift`: las 5 mayores y posiciones efectivas, 1/Σpeso²); en el anual, tesis al cierre
    (`theses_close`); en el trimestral, cambios de veredicto, 5 noticias y directivos;
  - Dividendos (solo anual, casi al final: en esta cartera de crecimiento es lo de menos): mes a mes, por empresa y
    frente al año anterior;
  - Revisión o carta de IA (no en los que están en curso).
- `display` prepara lo que se enseña sin cambiar lo guardado; `backfill_sections` rehace los trimestrales y anuales
  guardados sin alguna de las secciones de `SECTIONS` (al añadir una, ponerla ahí).
