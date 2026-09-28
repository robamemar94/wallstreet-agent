# NVIDIA — Investment Research Framework v2.7

*Versión Dashboard Medible · Documento preparado para Investment Research OS*

## Cuadro de mando — especificación operativa

**Principio:** cada KPI debe conectar una observación medible con una hipótesis y un mecanismo económico. El dashboard debe permitir detectar si la tesis se refuerza, permanece intacta, entra en vigilancia, se debilita o se rompe. Los umbrales son operativos y deben revisarse si cambia la economía del negocio.

| KPI | Actual | ■ Verde | ■ Amarillo | ■ Rojo | Hipótesis |
|---|---|---|---|---|---|
| FCF/share CAGR | Actualizar | >15% | 8–15% | <8% sostenido | H7 |
| Data Center revenue growth | +117% Q2 FY27 | >25% | 10–25% | <10% estructural | H1 |
| Gross margin | ~75% | ≥72% | 65–72% | <65% | H2/H4/H6 |
| AI accelerator share | Actualizar | ≥60% | 50–60% | <50% + deterioro económico | H2 |
| ASIC share of AI compute | Actualizar | <35% | 35–45% | >45% + mejor TCO | H4 |
| Cost/token vs alternatives | Benchmark | NVIDIA ≤ alternativa | 0–20% peor | >20% peor + migración | H4/H6 |
| Networking revenue growth | Actualizar | >20% | 10–20% | <10% + pérdida de wallet | H5 |
| Share count / dilution | ~24.3B diluidas | Estable o ↓ | 0–2% ↑ | >2–3% ↑ sostenido | H7 |
| SBC / FCF | Actualizar | <15–20% | 20–30% | >30% sostenido | H7 |
| Hyperscaler capex growth | Actualizar | >15% | 5–15% | <5% + demanda debilitándose | H1 |

**Regla de interpretación:** Un KPI rojo no rompe automáticamente la tesis. Se debe comprobar la cadena KPI → mecanismo económico → hipótesis → impacto en FCF/share → valoración. Un rojo aislado puede ser un headwind; varios KPIs relacionados en rojo y evidencia del mecanismo pueden llevar el estado a Weakening o Broken.

**Campos obligatorios por KPI:**

- Valor actual y fecha.
- Serie histórica y tendencia.
- Rango/umbral económicamente justificado.
- Semáforo y motivo.
- Hipótesis afectada.
- Mecanismo económico.
- Fuente primaria o fuente sectorial y limitaciones.
- Qué hecho cambiaría el semáforo.
- Impacto potencial sobre FCF/share y valoración.

---

*Tesis fundamental, narrativas estratégicas, valoración y sistema de seguimiento.*
- **Fecha de análisis:** 28/09/2026
- **Últimos resultados analizados:** Q2 FY2027, trimestre terminado el 26/07/2026
- **Cotización de referencia utilizada:** ~225 USD

## 0. Resumen ejecutivo

NVIDIA está evolucionando desde un líder de GPUs para AI hacia una plataforma completa de infraestructura de computación AI: GPU, CPU, networking, interconexión, sistemas y software. La tesis no depende simplemente de que la AI crezca, sino de que NVIDIA conserve una posición económicamente dominante dentro de la infraestructura AI y capture una parte elevada del valor creado.

El moat combina CUDA, ecosistema de desarrolladores, librerías, hardware, networking, integración de sistemas, escala y velocidad de innovación. La principal contra-tesis es la expansión de ASICs, especialmente en inference, si consiguen un TCO claramente superior y reducen los switching costs.

Estado actual: tesis empresarial INTACT/STRONG; valoración en WATCH. La variable crítica a vigilar es NVIDIA frente a ASICs en TCO, cuota, margen y FCF por acción.

## 1. Negocio y tesis central

### 1.1 Qué vende NVIDIA

NVIDIA vende capacidad de computación acelerada. El negocio ha evolucionado desde GPUs para gráficos hacia una plataforma de AI que integra aceleradores, CPUs, networking, sistemas y software.

Clientes: hyperscalers, AI clouds/NeoClouds, grandes empresas, AI labs, gobiernos y clientes soberanos.

La unidad económica relevante no es solo una GPU, sino el trabajo AI que puede realizar el sistema por dólar y por unidad de energía.

Variables económicas: performance, performance/$, performance/W, tokens/segundo, coste/token, utilización, networking y escalabilidad.

TAM y runway dependen de la expansión estructural de la computación AI, inference, AI factories, sovereign AI y enterprise AI.

### 1.2 AI factories

La estrategia Rubin amplía el alcance de NVIDIA desde el acelerador hacia una arquitectura completa: Rubin GPU, Vera CPU, NVLink, ConnectX, BlueField, Spectrum, Groq 3 LPX, sistemas y software.

La tesis económica es que NVIDIA puede aumentar su wallet share: pasar de vender principalmente aceleradores a capturar una mayor proporción del gasto total de una AI factory.

## 2. Moat, competencia y disrupción

### 2.1 Fuentes del moat

CUDA: software, APIs, librerías, herramientas y ecosistema de desarrolladores.

Switching costs: migrar código, optimizaciones, herramientas y workflows a otra arquitectura tiene costes.

Ecosistema: más desarrolladores y software aumentan el valor de la plataforma.

Co-design: hardware + software + networking + sistemas.

Escala y velocidad de innovación: sucesivas generaciones y capacidad de producción.

El moat no debe asumirse por liderazgo o tamaño. Debe comprobarse mediante cuota, pricing, márgenes, retención, TCO, performance/$, adopción de CUDA y FCF/share.

### 2.2 Competencia

| Competidor/alternativa | Ventaja | Amenaza para NVIDIA | KPI crítico |
|---|---|---|---|
| AMD | GPU directa, ROCm | Cuota y economics | Share + performance/$ + TCO |
| Google TPU | Custom silicon + Cloud + modelos | Integración vertical | Adopción + TCO |
| AWS Trainium/Inferentia | Custom silicon + AWS | Workloads propios | Uso + coste/token |

| Competidor/alternativa | Ventaja | Amenaza para NVIDIA | KPI crítico |
|---|---|---|---|
| Microsoft Maia | Custom silicon + ecosistema | Dependencia de terceros | Adopción + economics |
| ASICs especializados | Eficiencia en workloads concretos | Commoditización de inference | ASIC share + TCO |

### 2.3 Contra-tesis tecnológica

La contra-tesis principal es que los workloads AI se especialicen y los ASICs ofrezcan una economía claramente superior. La cadena que debe vigilarse es: amenaza → adopción → TCO → comportamiento del cliente → cuota → pricing → márgenes → FCF/share.

## 3. Narrativas estratégicas

### 3.1 Narrativa principal

La AI se convierte en una infraestructura crítica de la economía; la demanda de compute continúa creciendo; NVIDIA conserva liderazgo tecnológico y CUDA mantiene switching costs. La empresa aumenta wallet share mediante networking, CPUs, sistemas y software, capturando más economía por AI factory.

### 3.2 Contra-narrativa / abogado del diablo

La AI se commoditiza en parte. Los hyperscalers desarrollan ASICs propios para workloads estables, especialmente inference. El TCO inferior de soluciones especializadas reduce la demanda relativa de GPUs NVIDIA, presiona precios y márgenes y reduce wallet share.

### 3.3 Evidencia que discrimina

| Variable | Favorece narrativa NVIDIA | Favorece contra-tesis |
|---|---|---|
| Accelerator share | Estable/↑ | ↓ estructural |
| Performance/$ | Ventaja NVIDIA | ASIC claramente superior |
| Cost/token | Competitivo | ASIC claramente menor |
| CUDA | Switching costs intactos | Portabilidad creciente |
| ASIC share | Limitada/moderada | Crecimiento fuerte |
| Wallet share | ↑ | ↓ |
| Gross margin | >70% | <65% estructural |
| Networking | Gana importancia | Commoditización |
| FCF/share | Crecimiento fuerte | Estancamiento |
| Hyperscaler capex | Creciente | Desaceleración estructural |

## 4. Crecimiento, reinversión y capital allocation

### 4.1 Resultados recientes

| Métrica | Q2 FY27 |
|---|---|
| Revenue | $96,2B |
| Crecimiento YoY | +106% |
| Data Center | $89,0B |
| Data Center YoY | +117% |
| Gross margin | 75,0% |
| Operating income | $63,7B |
| Net income | $59,7B |
| H1 FY27 FCF | $69,9B |
| H1 FY27 CFO | $74,4B |

### 4.2 Capital allocation

Recompras: aproximadamente $26B devueltos a accionistas en Q2; quedaban aproximadamente $99B de autorización.

SBC: aproximadamente $2,03B en Q2 y $3,95B en H1.

Equity investments: aproximadamente $99B a 26/07/2026; compromisos de inversión de aproximadamente $25B.

Las recompras deben analizarse por precio pagado y efecto sobre FCF/share, no asumirse como creación automática de valor.

Las inversiones en el ecosistema deben evaluarse por demanda incremental, retornos y efecto sobre la economía por acción.

## 5. Calidad financiera

Gross margin: 75% en Q2 FY27.

Operating margin aproximado: 66%.

Conversión de caja extraordinaria.

La calidad futura depende de conservar pricing power y una economía superior frente a ASICs.

El principal riesgo financiero no es la capacidad actual de generar caja, sino que la normalización competitiva reduzca estructuralmente margen, crecimiento y FCF/share.

## 6. Management, incentivos y evidencia

La narrativa de management sobre aceleración de la demanda de AI está actualmente respaldada por los resultados: crecimiento superior al 100% en Data Center y margen bruto cercano al 75%. Aun así, las afirmaciones sobre rendimiento de Rubin deben contrastarse con benchmarks independientes.

Principio de seguimiento: management aporta información, pero los KPIs y la evidencia económica determinan si las hipótesis se fortalecen o debilitan.

## 7. Hipótesis críticas + KPIs

| Hipótesis | KPIs principales |
|---|---|
| H1 — La demanda de AI compute sigue creciendo estructuralmente | Hyperscaler capex; AI infrastructure spending; Data Center growth; utilización AI clouds |
| H2 — NVIDIA mantiene posición dominante | Accelerator share; NVIDIA vs AMD; NVIDIA vs ASICs; wallet share |
| H3 — CUDA mantiene switching costs | Adopción CUDA; ecosystem; workloads migrables; alternativas software |
| H4 — ASICs no destruyen la economía de NVIDIA | ASIC share; performance/$; cost/token; performance/W; custom silicon |
| H5 — NVIDIA aumenta wallet share | Networking revenue; CPU revenue; systems revenue; attach rate; AI factory revenue |
| H6 — Rubin mantiene liderazgo tecnológico | Benchmarks independientes; tokens/sec; cost/token; performance/W; time-to-deployment |
| H7 — El crecimiento se convierte en FCF/share | FCF; FCF/share; share count; SBC; capex; working capital |

## 8. Riesgos, incertidumbres y estado

| Riesgo | Clasificación actual | Qué lo convertiría en problema de tesis |
|---|---|---|
| Volatilidad bursátil / trimestre débil | Ruido | No aplica por sí solo |
| China | Headwind | Pérdida económica relevante y persistente fuera de China |
| AMD gana cuota | Headwind/material según magnitud | Cuota + economics deterioran conjuntamente |
| ASICs | Material / Watch | TCO superior + adopción + pérdida de wallet share |
| AI ROI insuficiente | Material | Capex AI estructuralmente desacelera |
| CUDA pierde switching costs | Thesis breaker | Migración significativa y coste de cambio bajo |
| Pérdida tecnológica | Thesis breaker | Dos generaciones con desventaja económica sostenida |
| FCF/share se estanca | Thesis breaker | Estancamiento estructural pese a crecimiento del mercado |

## 9. Valoración

Base utilizada: H1 FY27 FCF de ~$69,9B, anualizado ≈ $139,8B. Con ~24,34B acciones, FCF/share anualizado ≈ $5,74. A ~$225 por acción, el múltiplo sobre FCF anualizado es ≈39x.

| Escenario | FCF/share CAGR | Múltiplo terminal | Precio 2036 | CAGR precio |
|---|---|---|---|---|
| Bear | 8% | 22x | ~$273 | ~1,9% |
| Base | 15% | 30x | ~$697 | ~12,0% |
| Bull | 20% | 35x | ~$1.245 | ~18,7% |

Estos escenarios no son previsiones. Sirven para estudiar la sensibilidad del retorno de la acción a crecimiento de FCF/share y múltiplo terminal. La valoración debe actualizarse con cada trimestre.

## 10. Dashboard NVIDIA

| KPI | Qué queremos detectar |
|---|---|
| Data Center revenue growth | Demanda |
| Gross margin | Pricing power / mix |
| FCF/share growth | Creación de valor por acción |
| Accelerator market share | Moat |
| ASIC share | Disrupción |
| Performance/$ | Competitividad |
| Cost/token | Economía AI |
| Networking revenue/share | Wallet share |
| SBC / FCF + share count | Dilución |
| Hyperscaler capex / AI ROI | Demanda futura |
| CUDA switching cost | Durabilidad del moat |

## 11. Estado de la tesis

| Área | Estado |
|---|---|
| Negocio | Strengthening |
| Moat | Intact / Strengthening |
| ASICs | Watch |
| Calidad financiera | Strengthening |
| Capital allocation | Watch |
| Valoración | Watch |

## 12. Tesis maestra

NVIDIA no es principalmente una apuesta por que se vendan muchas GPUs. Es una apuesta por que la computación AI se convierta en una infraestructura crítica de la economía y NVIDIA conserve una posición económicamente dominante dentro de esa infraestructura.

El moat procede de la combinación de CUDA, ecosistema, software, hardware, networking, integración de sistemas, escala y velocidad de innovación. La estrategia Rubin aumenta potencialmente el valor capturado por AI factory al ampliar el wallet share de NVIDIA más allá del acelerador.

La principal contra-tesis no es AMD: son los ASICs y la eventual commoditización de determinados workloads, especialmente inference. La cuestión decisiva será si la flexibilidad y el ecosistema NVIDIA continúan compensando el coste potencialmente inferior de soluciones especializadas.

Actualmente los resultados siguen respaldando fuertemente la tesis: Data Center crece >100%, margen bruto ~75% y FCF extraordinario. Pero a una cotización cercana a $225, la acción requiere que una parte importante de esta economía excepcional persista durante muchos años.

## 13. Cuadro de mando definitivo — KPIs de NVIDIA

El cuadro de mando es parte integral de la tesis. No es una colección de métricas: cada KPI existe porque permite confirmar, debilitar o refutar una hipótesis y/o explicar una variable de valoración. Los KPIs se mantienen estables entre actualizaciones para construir series históricas; solo se modifican si cambia materialmente la tesis.

### 13.1 KPIs maestros

1. **FCF per Share Growth — creación de valor por acción.**
2. **Data Center Revenue Growth — demanda estructural de AI.**
3. **Gross Margin — pricing power, mix y presión competitiva.**
4. **Accelerator / AI Compute Market Share — posición competitiva.**
5. **ASIC Economics — Cost per Token / TCO frente a NVIDIA.**

### 13.2 KPIs secundarios

- Data Center Revenue — tamaño y evolución absoluta del negocio AI.
- FCF y FCF Margin — generación y conversión de caja.
- Share Count / Net Dilution — efecto de SBC y recompras.
- NVIDIA vs AMD Performance/$ — competitividad frente a GPU directa.
- NVIDIA vs ASIC Performance/$ — competitividad frente a soluciones especializadas.
- Cost per Token — economía de inference.
- ASIC Share of AI Compute — penetración de soluciones especializadas.
- Networking Revenue Growth — crecimiento alrededor del acelerador.
- Networking Share / Attach Rate — evolución del wallet share.
- CUDA / Developer Ecosystem — durabilidad del switching cost.
- Hyperscaler Capex — demanda futura de infraestructura.
- AI ROI / Revenue generated per AI Capex — retorno económico del capex de clientes.
- SBC / FCF — calidad del FCF frente a remuneración basada en acciones.
- Capex + Strategic Investments / FCF — intensidad de reinversión.
- Incremental FCF / Incremental Capital — retorno de la reinversión.

### 13.3 Mapa KPI → hipótesis → mecanismo económico

| KPI | Hipótesis | Mecanismo económico |
|---|---|---|
| FCF/share growth | H7 | Crecimiento operativo → valor por acción |
| Data Center growth | H1 | Demanda de AI compute |
| Gross margin | H2/H4/H6 | Pricing power y ventaja económica |
| Accelerator share | H2 | Posición dominante |
| Cost/token / TCO | H4/H6 | Economía frente a ASICs |
| ASIC share | H4 | Riesgo de sustitución |
| Networking growth / attach | H5 | Wallet share de AI factories |
| CUDA ecosystem | H3 | Switching costs |
| Hyperscaler capex | H1 | Demanda futura |
| AI ROI | H1/H4 | Justificación económica del capex |
| SBC / share count | H7 | Conversión a valor por acción |
| Incremental FCF / capital | H5/H7 | Retorno de reinversión |

### 13.4 Campos obligatorios de cada KPI en nuestro Investment Research OS

- Valor actual.
- Serie histórica suficiente para identificar tendencia.
- Umbral o rango relevante, solo cuando exista una base económica.
- Tendencia: acelerando / estable / desacelerando.
- Semáforo: verde = refuerza; amarillo = ambiguo/monitorizar; rojo = debilita o puede refutar.
- Hipótesis afectada.
- Mecanismo económico que conecta el KPI con la tesis.
- Fecha de actualización.
- Fuente y enlace/documento de origen.
- Limitaciones de la métrica.
- Qué hecho cambiaría el semáforo.

### 13.5 Regla de estabilidad del dashboard

No añadiremos KPIs por disponibilidad de datos ni eliminaremos KPIs porque sean incómodos. El conjunto cambia solo si cambia la tesis, aparece una nueva variable económicamente material o una métrica deja de aportar capacidad real para entender, demostrar, refutar, valorar o monitorizar.

### 13.6 Umbrales operativos del dashboard

El objetivo no es fijar objetivos arbitrarios. Cada KPI debe tener, cuando sea económicamente defendible, un rango verde, un rango amarillo y un nivel rojo. Los umbrales se revisarán solo cuando cambie la economía del negocio, la estructura competitiva o la tesis.

| KPI | Valor actual | ■ Verde | ■ Amarillo | ■ Rojo | Hipótesis |
|---|---|---|---|---|---|
| FCF/share CAGR | Actualizar | >15% | 8–15% | <8% sostenido | H7 |
| Data Center revenue growth | +117% Q2 FY27 | >25% | 10–25% | <10% estructural | H1 |
| Gross margin | ~75% | ≥72% | 65–72% | <65% | H2/H4/H6 |
| AI accelerator share | Actualizar | ≥60% | 50–60% | <50% + deterioro económico | H2 |
| ASIC share of AI compute | Actualizar | <35% | 35–45% | >45% + mejor TCO | H4 |
| Cost/token vs alternatives | Benchmark | NVIDIA ≤ alternativa | 0–20% peor | >20% peor + migración | H4/H6 |
| Networking revenue growth | Actualizar | >20% | 10–20% | <10% + pérdida de wallet | H5 |
| Share count / dilution | ~24.3B diluidas | Estable o ↓ | 0–2% ↑ | >2–3% ↑ sostenido | H7 |
| SBC / FCF | Actualizar | <15–20% | 20–30% | >30% sostenido | H7 |
| Hyperscaler capex growth | Actualizar | >15% | 5–15% | <5% + demanda debilitándose | H1 |

### 13.7 Regla de interpretación

Un KPI rojo no rompe automáticamente la tesis. Primero se comprueba la cadena KPI → mecanismo económico → hipótesis → impacto en FCF/share → valoración. Un rojo aislado puede ser un headwind; varios KPIs relacionados en rojo y una confirmación del mecanismo pueden llevar el estado a Weakening o Broken.

## 14. Preguntas maestras para la próxima actualización

¿Ha cambiado la trayectoria de Data Center y sus motores?

¿Está cambiando la cuota económica frente a AMD y ASICs?

¿Se mantiene el switching cost de CUDA?

¿Rubin mantiene ventaja real en TCO, performance/$ y cost/token?

¿Está aumentando NVIDIA su wallet share mediante networking, CPU y sistemas?

¿FCF/share crece al ritmo esperado después de SBC y recompras?

¿Las inversiones del ecosistema generan retornos económicos?

¿Ha cambiado el retorno sobre el capex AI de los clientes?

¿Qué evidencia nueva favorece la narrativa principal y cuál favorece la contra-narrativa?

**Nota metodológica:** Los datos de resultados y valoración incluidos aquí corresponden al análisis realizado para esta tesis. Las cifras de valoración son escenarios y no predicciones. En cada actualización deberán sustituirse por los datos más recientes y contrastarse con fuentes primarias.
