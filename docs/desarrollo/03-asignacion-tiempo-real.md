# 03 — Asignación en tiempo real

`packages/dispatch-engine/src/dispatch_engine/policies/neediest_shovel.py`
y `domain/snapshot.py`

## Qué se construyó

`NeediestShovelPolicy`, una forma reducida del procedimiento de asignación de camiones vacíos de la
patente US 11,187,547.

El snapshot reproduce las listas del procedimiento:

| Patente | Código |
|---|---|
| `Ta(s)` — camiones en la pala, yendo o proyectados | `MineSnapshot.arrivals(shovel_id)` |
| `T'` — camiones que necesitan asignación pronto | `MineSnapshot.trucks_needing_assignment()` |
| `Tc(s)` — camiones despachables a esa pala | `MineSnapshot.candidates_for(shovel_id)` |

Y el algoritmo sigue el mismo bucle:

1. Acarreo **requerido** por pala (del plan de producción) contra el **asignado** (suma de payloads
   de los camiones en ella o en camino). Necesidad = requerido − asignado.
2. Ordenar las palas por necesidad decreciente.
3. Para la pala más necesitada, elegir el camión candidato de **menor penalidad**, donde la penalidad
   es el tiempo ocioso total que provocaría el par: pala esperando camión + camión haciendo cola.
4. Si el elegido no es el camión que pidió destino, se lo asigna en un **libro virtual** (sube el
   acarreo comprometido, se posterga el instante en que la pala queda libre) y se repite.
5. Solo se confirma la asignación del camión solicitante.

## Decisiones

**La necesidad se mide en toneladas y la aporta el plan.** `necesidad = plan.required_haulage_t(pala)
− comprometido_t`. La política ya no sabe cómo se calcula ese requerimiento: `LpProductionPlan` lo
deriva del tiempo de ciclo (ley de Little) y `StaticProductionPlan` lo aproxima con una ventana fija
de 30 minutos (`--horizon-min`). Ese corte es lo que permitió meter el LP sin tocar esta etapa.

**Las palas por encima del plan siguen en el ranking, con necesidad negativa.** Si se las excluyera,
un camión que pide destino cuando todas están servidas se quedaría sin asignación y entraría en
standby para siempre. "La pala más necesitada" puede ser la menos sobre-servida.

**Los empates de necesidad se rompen por prioridad de pala y después por id**, no por penalidad. Es
deliberado y sigue la lógica de la patente: primero se elige la pala, después el mejor camión para
ella. La prioridad queda como la palanca explícita del despachador para desempatar.

**La intervención manual va en el snapshot, no en la política.** `Overrides` (camión fijado a pala,
camiones excluidos, palas excluidas) viaja dentro de `MineSnapshot`, así que cualquier política
futura la recibe sin tener que reimplementarla. El camión fijado corta el algoritmo antes del
ranking y devuelve `reason="locked by dispatcher"`.

**La asignación explica por qué.** `Assignment` lleva `reason` y `penalty_s`. Un despacho que no se
puede explicar es un despacho que la operación termina desactivando.

## El sesgo de reparto y cómo lo corrigió el LP

**El problema (con plan estático).** Midiendo la necesidad como `tasa × ventana fija`, el acarreo
comprometido cuenta solo los camiones asignados *en ese instante*: cuando un camión sale cargado de
la pala deja de contar de inmediato, así que una pala con ciclo corto recupera necesidad más rápido
y vuelve a encabezar el ranking. El resultado era una adhesión al plan muy despareja.

**La corrección.** El LP entrega el requerimiento derivado del tiempo de ciclo, no de una ventana
arbitraria: una pala lejana necesita *más* toneladas comprometidas para sostener la misma tasa,
justamente porque sus camiones tardan más en volver.

Corriendo el escenario de juguete 2 horas, adhesión al plan (real ÷ planificado):

| Pala | Plan estático | Plan LP |
|---|---|---|
| SH01 | 990 / 1.400 = **71 %** | 1.430 / 1.610 = **89 %** |
| SH02 | 440 / 1.000 = **44 %** | 440 / 537 = **82 %** |
| SH03 | 1.430 / 1.600 = **89 %** | 990 / 800 = **124 %** |

Y a igual tonelaje total (la flota es el límite en ambos casos), el chancador recibe 3.520 t con el
LP contra 2.640 t con el plan fijo.

**Lo que queda.** La flota es discreta y el plan es continuo. SH02 necesita 256 t en vuelo, o sea
1,16 camiones de 220 t; en la práctica sostiene 1, que rinde unas 460 t/h contra las 537 t/h del
plan, y el excedente lo absorbe SH03 (124 %). Con una flota más grande o camiones más chicos
respecto del plan, el redondeo pesa menos.

## Dos hallazgos que salieron de correrlo, no de leerlo

**La ejecución responde al plan por escalones, no de forma continua.** El ranking solo usa el *orden*
de las palas por necesidad, no la distancia entre ellas, así que un cambio de plan que no reordena la
lista no cambia una sola asignación. Medido con el margen de mezcla sobre `toy`: pasar el plan de ley
0,800 a 0,770 —un cambio del 30 % en el tonelaje pedido a SH02— dio una corrida **idéntica**, y recién
a 0,750, cuando SH02 supera a SH03 en el ranking, el comportamiento saltó. Explica buena parte de la
mala adhesión de SH02 documentada arriba, y sugiere que la elección del par camión/pala debería pesar
la magnitud de la necesidad y no solo tomar la primera.

**`shovel_idle_weight` es contraproducente cuando la flota es el cuello de botella.** Subirlo de 1 a
5 sobre `toy` costó 63 % más de cola de camiones (24,8 → 40,5 min), **empeoró** el ocioso total de
palas (suma de utilización 113 % → 110 %) y empujó la ley de 0,806 a 0,820. No hubo trade-off: hubo
pérdida pura. Con palas al 20-60 % de utilización el ocioso no lo causa el despacho sino la falta de
camiones, y forzar la palanca solo concentra la flota en una pala. El comentario original en el
código decía "subilo para mantener las palas ocupadas" sin esa condición, lo que lo volvía engañoso;
está corregido.

## Otras simplificaciones

- **El camión que está cargando se cobra el tiempo de carga completo**, porque el snapshot no lleva
  el progreso de la carga. Es un pesimismo leve que sesga al motor en contra de palas que recién
  empezaron a cargar.
- **No están las restricciones operativas** de la patente: acarreos cortos (`SH_PARAM`), reducción de
  velocidad y reducción de carga, que en el sistema real alteran la pertenencia a las listas
  candidatas y las ETA.
- **La cola en la pala se estima secuencialmente** (los camiones comprometidos se atienden en orden
  de llegada), sin considerar que uno pueda desviarse antes de llegar.

## Verificación

`packages/dispatch-engine/tests/test_neediest_shovel.py` cubre:

- que la necesidad le gana a la cercanía (el camión va a la pala lejana si es la más atrasada);
- que el acarreo ya comprometido reduce la necesidad y vuelca el siguiente camión a la otra pala;
- que el empate se rompe por prioridad de pala;
- que el lookahead deja la pala más necesitada al camión que llega antes y manda al solicitante a la
  segunda;
- que el bloqueo manual, la exclusión de palas y la exclusión de camiones se respetan;
- que una pala por encima del plan igual recibe a un camión que necesita trabajo.
