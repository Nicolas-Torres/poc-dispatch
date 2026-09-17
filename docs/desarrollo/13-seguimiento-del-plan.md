# Etapa 13 — Dos defectos que solo aparecieron midiendo

El pendiente en la lista era *"que el LP conozca la granularidad de la flota: un modelo entero-mixto
en lugar de un LP, para que deje de proponer proporciones que exigen fracciones de camión"*. La
hipótesis escrita antes de implementar decía que el margen de mezcla de 0,05 de la
[etapa 11](11-plan-ejecutable.md) era un parche que compensaba esa granularidad.

**La hipótesis era falsa, y medirla destapó dos defectos reales que no tenían nada que ver con ella.**

## Por qué el encuadre original estaba mal

Primero lo obvio: el LP sí reparte camiones fraccionarios. Todas las rutas de `toy` piden fracciones:

```
  zone_n -> crusher      1327.3 t/h    2.640 camiones
  zone_s -> crusher       796.4 t/h    1.724 camiones
  zone_w -> waste_dump    800.0 t/h    1.636 camiones
                          TOTAL        6.000 camiones
```

Pero eso **no es un problema**: en DISPATCH un camión no está dedicado a una ruta, se reasigna cada
ciclo. 1,724 camiones sobre una ruta es perfectamente ejecutable repartiendo el tiempo de un camión
entre dos rutas. Forzar `n_r` entero por ruta habría restringido el modelo **más** que la realidad, no
menos. El modelo entero-mixto propuesto se descartó antes de escribirlo, por razones de modelado.

La segunda explicación candidata era la cuantización en cargas discretas: la mezcla se entrega de a
220 t, así que la proporción realizada se redondea. Esa sí es una hipótesis medible, y tiene una
firma clara: el error tendría que **encogerse** con el horizonte.

Se midió `toy` con margen 0,00 a horizontes crecientes:

| horizonte | ley entregada al chancador |
|---|---|
| 2 h | 0.8000 |
| 4 h | 0.8059 |
| 8 h | 0.8405 |
| 24 h | 0.8767 |
| 72 h | 0.8921 |

El error **crece** monótonamente. No es ruido de cuantización: es un sesgo sistemático. Las dos
explicaciones quedaron descartadas por medición, no por opinión.

## Hallazgo 1 — la asignación seguía el plan como un stock, sin término integral

Mirando adhesión por pala en lugar de solo la ley, el sesgo tiene forma: **una pala se muere de
hambre, y empeora sin techo**.

| horizonte | SH01 | SH02 | SH03 |
|---|---|---|---|
| 4 h | 99 % | 92 % | 110 % |
| 8 h | 113 % | **56 %** | 107 % |
| 24 h | 121 % | **22 %** | 113 % |

La causa está en `_rank_by_need`: el ranking compara

```
required_haulage_t(s)  -  assigned_haulage_t(s)
```

y `assigned_haulage_t` suma los camiones que van hacia la pala **en este instante**. Es un *stock*.
El ranking es entonces un **controlador proporcional puro sobre un stock, sin acción integral**: con
una flota discreta el déficit de stock se satisface en una asignación entera que no tiene por qué
respetar la proporción planificada, y como nada se acumula, el faltante *acumulado* crece sin límite.
Una pala crónicamente mal servida nunca junta presión para que la sirvan.

Es fiel a la patente —el procedimiento de US 11,187,547 compara stocks—, pero en esta forma reducida
la consecuencia es deriva sin techo.

**El arreglo** es un término integral. El plan promete una *tasa*, así que el error natural es
acumulado: toneladas que el plan esperaba de la pala hasta ahora, menos las que realmente excavó. La
simulación lleva ese libro mayor (`_planned_t` / `_dug_t`, integrando la tasa solo mientras el plan
estuvo vigente) y lo publica en el snapshot como `plan_shortfall_t`; la política lo suma al ranking.

Una pala fuera de servicio tiene tasa requerida cero, así que **no acumula deuda durante su parada**:
si no, la flota se le tiraría encima apenas volviera. Hay un test para eso.

## Hallazgo 2 — el gemelo no hacía cumplir la capacidad del destino

Al arreglar el hallazgo 1, la adhesión saltó a 99-101 %... y el valor entregado **cayó 13 %**. Según
el protocolo eso obliga a revertir. Antes de revertir, se midió por qué.

`capacity_tph` aparecía **solo en `lp.py`**. La simulación nunca la hacía cumplir: el único límite
físico en una descarga eran las bahías y el tiempo de volteo. El chancador de `toy` tiene capacidad
nominal de 2.200 t/h y dos bahías de 60 s, es decir **26.400 t/h físicos**.

Medido, la política que deriva alimenta el chancador **por encima de su capacidad nominal**:

| política | 4 h | 8 h | 24 h |
|---|---|---|---|
| neediest | 100 % | 105 % | **116 %** |
| earliest | 68 % | 68 % | 68 % |

O sea: el valor extra de la política con deriva era **ficticio**. Cobraba por toneladas que el
chancador no podía procesar. El "13 % de pérdida" al arreglar el hallazgo 1 no era una pérdida: era
dejar de facturar material imaginario.

**El arreglo** es un limitador de tasa en la admisión del destino (`_admit_to_intake`). Las bahías
siguen siendo el acto físico de voltear; el limitador es la línea de proceso detrás. Un destino sin
`capacity_tph` declarada no se toca.

Los dos defectos son causalmente inseparables: arreglar solo el 1 parece una regresión de valor, y
arreglar solo el 2 deja la deriva. Van en el mismo cambio.

## Resultados

**Adhesión al plan**, que deja de degradarse con el horizonte:

| | 4 h | 8 h | 24 h |
|---|---|---|---|
| peor pala, antes | 69 % | 65 % | **42 %** |
| peor pala, después | 89 % | 93 % | **92 %** |

Queda por debajo de 100 % porque el chancador ahora limita de verdad: el plan pide más de lo que la
admisión acepta. Eso es honesto, no un defecto.

**Comparación de políticas** (`toy`, 4 h, 5 réplicas). Los valores absolutos bajan parejo en las tres
políticas, porque es valor ficticio que se retira de todas por igual:

| | antes | después |
|---|---|---|
| valor `neediest` | 33.880 | 29.920 |
| valor `earliest` | 26.840 | 23.100 |
| **ventaja de seguir el plan** | **+26 %** | **+30 %** |
| ley entregada por `earliest` | 0.767 en spec | **0.858 fuera de spec** |

En `toy-stockpile` la ventaja pasa de +22 % a +29 %. **El gemelo se volvió más honesto y la tesis del
proyecto salió más fuerte, no más débil.**

**Bajo ruido** (`toy-variable`, 8 h, 10 réplicas) aparece el resultado más contundente:

| | neediest | earliest | longest |
|---|---|---|---|
| valor | 54.648 ± 8.248 | 47.498 ± 5.480 | 51.612 ± 2.303 |
| ley al chancador | **0.755 ± 0.002** | 0.816 ± 0.106 | 0.706 ± 0.042 |

Seguir el plan no solo entrega mejor ley en promedio: entrega **50 veces menos dispersión** en la ley.
Para una planta concentradora esa varianza es el problema, no la media.

**Cola en la descarga**: pasa de 0,0 min —siempre, en todos los escenarios— a 202,1 min en `toy` a
4 h. El KPI existía pero nada podía producirlo. Esto desbloquea el pendiente de *"que la decisión de
destino mire la cola en la descarga"*, que hasta ahora era imposible de demostrar.

## Sobre el margen de mezcla

La hipótesis decía que el margen de 0,05 dejaría de hacer falta. **No es así, y ahora se entiende por
qué.** Con margen 0,00 y los dos arreglos, la ley entregada ya no diverge:

| horizonte | antes | después |
|---|---|---|
| 8 h | 0.8405 | 0.8086 |
| 24 h | 0.8767 | 0.8078 |
| 72 h | 0.8921 | 0.8065 |

El error pasó de deriva sin techo a un sesgo pequeño y acotado. Pero sigue apenas por encima del
techo de 0,80, porque un LP que maximiza valor **se para exactamente encima** de la restricción que
lo limita, y no deja lugar a error de ejecución alguno.

O sea: el margen sigue siendo necesario, pero por la razón que su propio docstring ya declaraba
—sacar al plan del borde de la ventana— y no para tapar un bug de seguimiento. Es la diferencia entre
una constante calibrada a mano y una decisión de diseño.

## Lo que cambió

| Archivo | Cambio |
|---|---|
| `dispatch_engine/domain/snapshot.py` | `plan_shortfall_t` en el snapshot |
| `dispatch_engine/policies/neediest_shovel.py` | término integral en `_rank_by_need` |
| `mine_sim/simulation.py` | libro mayor del plan y limitador de admisión del destino |
| `packages/mine-sim/tests/test_plan_tracking.py` | **nuevo**: 8 tests sobre los dos defectos |

Los tests nuevos cubren lo que ninguna suite verde habría detectado antes: que ninguna pala baje del
80 % del plan **a ningún horizonte**, que la adhesión no decaiga al alargar el turno, que un destino
nunca reciba por encima de su capacidad nominal, y que una parada de pala no genere deuda.

## Pendiente que esto deja abierto

- **Reconsiderar el modelo entero-mixto por otra razón.** Se descartó porque los camiones no están
  dedicados a rutas. Si alguna vez se modelan restricciones que sí son enteras (cuántas palas operar,
  qué destinos abrir), vuelve a tener sentido.
- **Que la decisión de destino mire la cola**, ahora que las colas existen.
- **Correlación entre eventos** (la lluvia enlentece todos los viajes a la vez).
