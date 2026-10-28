# Roboadvisor

`/roboadvisor` (entrada «Robo» del menú, bajo Cartera). Vista: `robo_views.py`. Plantilla: `roboadvisor.html`.
Servicio: `robo_service` (ver «Roboadvisor» en `app/CLAUDE.md`). API: `/api/portfolio/roboadvisor…`.

Es información aparte de la cartera de acciones: solo suma al Patrimonio del Resumen de Cartera (`js/roboadvisor.html`).

## Pestañas (en la dirección: `#meses`, `#fondos`, `#aportaciones`, `#ajustar`)
- **Resumen:** cinco cifras (valor estimado «~» o ajustado, invertido, ganancia, TIR, aportación) y, debajo, una banda
  de periodos como la de Cartera (`periods`): último día, este mes, 3 meses, este año (con el índice de MyInvestor) y
  12 meses, en € y %.
  - **Último día:** el último liquidativo que tienen **todos** los fondos frente al anterior, ponderado (no mezcla el
    viernes de uno con el lunes de otro). La etiqueta es ese día («vie 23/10»); si no es la última sesión, va atenuado y
    con borde discontinuo (`stale`), y el globo dice cuál falta y qué fondos van por detrás. El Valor dice también de
    qué día es el liquidativo (`nav_date` = el que tienen todos). En Fondos, el que va por detrás lleva su fecha.
  - **Evolución** con modo, periodo y escala en la dirección (`?modo=gain&periodo=1y&log=1`; 6M · YTD · 1A · 3A · Todo).
    De serie, como en bancos y roboadvisors: Valor frente a aportado, lineal y desde el inicio (decidido el 25/10/2026).
    La escala logarítmica es opcional y solo en Valor:
    - Valor: valor a fin de mes frente a lo aportado en escalón (relleno = lo ganado);
    - Ganado: solo valor − aportado (se lee bien en cualquier periodo);
    - %: sin el efecto de las aportaciones, rebasado al inicio del periodo: el robo (los % mensuales de MyInvestor,
      cada año calibrado a su % anual), el MSCI World en euros (`IWDA.AS`) y tu cartera de acciones (valor por
      participación del Rendimiento; empieza más tarde y sale desde el nivel del robo en su primer mes).
  - **Por años:** robo frente al índice de MyInvestor (barras finas y diferencia en pp). **Dónde está tu dinero:** zonas.
- **Por meses:** mapa de calor año × mes (% o €) con el total y el índice de cada año; borde discontinuo = calculado
  aquí (mes en curso). Mejor y peor mes, meses en positivo.
- **Fondos:** cada fondo (peso, valor aproximado, 1M · 3M · YTD · 1A del liquidativo y su curva de un año) y la
  exposición por zonas en anillo (`FUND_ZONES`; el Developed World con pesos aproximados del MSCI World). Al pulsar un
  fondo se abre su ficha en una ventana (liquidativo, gráfica con 6M · 1A · 3A · 5A, 1S–5A, máximo y mínimo de 52
  semanas, desde el máximo, peso, valor, enlace a Yahoo); va en la dirección (`?fondo=ISIN`). Los tres de iShares son
  clases nuevas: en Yahoo solo hay histórico desde agosto de 2025 (lo dice la ficha).
- **Aportaciones:** totales por año; barras por mes con lo estimado apilado en gris (eje con tope: las extra salen
  cortadas con su importe arriba, para no aplastar las habituales); las últimas, agrupadas por año,
  sin scroll: las que caben a la altura de la gráfica y «Ver más» para las anteriores.
- **Ajustar:** aportación mensual (desde un mes), ajuste con MyInvestor (o pegando su resumen) y desvío de cada ajuste.

Colores de series: paleta validada del skill de visualización (azul robo, naranja índice, aguamarina cartera; zonas +
amarillo y rosa), con sus tonos de modo oscuro. Las zonas llevan siempre nombre y % escritos (contraste bajo en claro).

## Datos de partida (25/10/2026)
- Tabla de rentabilidades 2023-2026 de MyInvestor (los negativos salen en rojo sin signo en la app: ojo al pasarla).
- Aportaciones reales desde feb-2023 (lista del usuario). Una estimada para cuadrar con su capital invertido: 2.519,30 €
  el 01/05/2026 (el usuario recuerda «unos 2.500 € en primavera»; desde mayo el capital real es un 12 % mayor que el de
  la lista). Si aparece la fecha real, se cambia en `settings['robo'].contributions`.
