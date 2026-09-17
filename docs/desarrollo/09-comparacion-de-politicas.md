# 09 — Comparación de políticas

`packages/dispatch-engine/src/dispatch_engine/policies/` (`common.py`, `earliest_shovel.py`) y
el comando `dispatch-cli compare`

## Qué se construyó

Desde la [etapa 0](00-workspace-y-arquitectura.md) el `Protocol` `DispatchPolicy` existe para que las
estrategias de asignación sean intercambiables, pero **nunca se había intercambiado ninguna**: la
propiedad estaba afirmada y no ejercitada. Y sin una referencia contra la cual medir, tampoco había
forma de decir cuánto compra el enfoque de dos etapas.

- `EarliestShovelPolicy`: la heurística "**1 camión para n palas**" con la que la literatura
  contrasta DISPATCH (Alarie y Gamache, 2002). Mira un camión contra todas las palas y minimiza
  **su propia espera**, ignorando el plan de producción y los demás camiones que van a pedir destino.
- `policies/common.py`: la aritmética de colas y el reparto de destinos, compartidos por ambas
  políticas.
- `dispatch-cli compare`: corre todas las políticas sobre la misma mina y la misma flota, y las pone
  lado a lado.

## Decisiones

**Las dos políticas comparten la elección de destino.** Una heurística simple no tiene plan, así que
lo natural sería que también eligiera el destino más cercano — pero entonces la comparación mezclaría
dos efectos y no se sabría cuál explica la diferencia. `EarliestShovelPolicy` recibe el plan y lo usa
**solo para destinos**, de modo que lo único que cambia entre las dos corridas es la decisión de
pala. El precio es que la baseline es un poco más capaz que su versión de manual; a cambio, el
resultado es atribuible.

**La métrica de comparación es el valor realizado, no el tonelaje.** `plan value` recalcula `c'x`
sobre lo que efectivamente se movió: toneladas × valor de la pala × valor del destino, exactamente el
objetivo que maximiza el LP. El tonelaje solo no distingue entre mover mineral y mover estéril.

**Los helpers compartidos son públicos, no privados.** `shovel_free_at_s`, `arrival_at_shovel_s` y
`plan_tracking_destination` son los ladrillos para escribir una política nueva. Marcarlos privados
habría obligado a copiarlos a quien quiera probar otra estrategia.

## Resultado

Cuatro horas sobre la mina con stockpile, misma flota, mismo plan, misma regla de destinos:

```
  metric                    neediest    earliest
  tonnes moved                11,000      11,000
  plan value                  29,304      23,804
  cycles                          50          50
  truck queueing (min)          43.1        22.6
  shovel idle (min)              448         454
  crusher cu                   0.783       0.689
    window               0.60 - 0.80
```

Y sobre la mina base, donde el contraste es todavía más nítido:

```
  metric                    neediest    earliest
  tonnes moved                10,780      11,000
  plan value                  31,460      26,840
  truck queueing (min)          24.8        16.7
  crusher cu                   0.806       0.767
```

**La baseline hace exactamente lo que promete y por eso pierde.** Minimiza la espera del camión —
tiene casi la mitad de tiempo de cola— y en la mina base incluso **mueve más toneladas**. Pero gana
entre 17 % y 23 % menos valor, porque manda los camiones a la pala que los atiende antes en lugar de
a la que el plan necesita: más estéril, menos mineral de alta ley, y la ley entregada al chancador se
aleja del objetivo.

Es el resultado que justifica la arquitectura de dos etapas: **mover más material más rápido no es el
objetivo**. Sin un plan contra el cual medirse, una política no tiene forma de saberlo.

## La segunda baseline: repartir parejo

`LongestWaitingShovelPolicy` es la otra heurística "1 camión para n palas" de la literatura: manda el
camión a la pala que lleva más tiempo sin recibir uno. Donde `earliest` optimiza para el **camión**,
esta optimiza para las **palas** — reparte la flota pareja — pero es igual de ciega al plan: un
reparto parejo solo es el correcto si todas las palas valen lo mismo.

24 horas sobre `toy-variable`, 10 réplicas:

```
  metric                     neediest          earliest           longest
  tonnes moved        66,726 +/-2,397   67,342 +/-1,815   65,736 +/-2,180
  plan value        191,004 +/-18,353 150,700 +/-11,439  172,678 +/-7,757
  truck queueing (min)  144.7 +/-63.4      62.2 +/-25.6      73.9 +/-18.6
  shovel idle (min)       2,823 +/-58       2,852 +/-55       2,772 +/-60
  crusher cu           0.840 +/-0.008    0.801 +/-0.045    0.698 +/-0.006
```

**Cae entre las dos, como cabía esperar**: repartir parejo se acerca más al plan que ir a la pala más
cercana, pero sigue sin ser el plan. Y hace exactamente lo que promete — tiene el menor ocioso de
palas de las tres.

Dos observaciones que no anticipé:

- **Es la política más predecible.** Su dispersión de valor es menos de la mitad que la de
  `neediest` (±7.757 contra ±18.353). Repartir parejo no rinde tanto, pero rinde parecido todos los
  días. Para una operación que valore la previsibilidad por encima del promedio, ese es un argumento
  real.
- **Es la que mejor cumple la ley, y por accidente.** Entrega 0,698 ± 0,006, cómodamente dentro de la
  ventana, mientras que `neediest` entrega 0,840 y se sale. La causa es que al repartir parejo entre
  los dos bancos de mineral la mezcla tiende al punto medio de las leyes, mientras que la política que
  sigue el plan persigue un plan **parado sobre el límite** (ver [02](02-plan-de-produccion.md)). La
  política de mayor valor es la que más incumple la especificación de la planta, y no porque sea peor
  sino porque el plan que persigue no dejó margen.

## Limitaciones

- **`compare` fija el plan LP y los parámetros por defecto.** No permite comparar, por ejemplo, la
  misma política con distintos `--shovel-idle-weight`.
- La baseline **también hereda la replanificación**: si una pala cae, su plan de destinos cambia. Es
  coherente con aislar la decisión de pala, pero la aleja un poco más de la heurística pura.

## Verificación

```bash
uv run dispatch-cli compare --scenario toy-stockpile --hours 4
uv run dispatch-cli run --scenario toy --hours 4 --policy earliest
```

`packages/dispatch-engine/tests/test_policy_contrast.py` cubre que las dos políticas eligen palas
distintas sobre el mismo snapshot —la que sigue el plan toma el viaje largo hacia la pala que se está
quedando sin camiones, la baseline va a la más cercana— y que la baseline respeta igual los bloqueos
y exclusiones del despachador. `packages/dispatch-engine/tests/test_destinations.py` corre todos sus
casos contra **ambas** políticas, que es lo que prueba que comparten la elección de destino.
`packages/mine-sim/tests/test_simulation.py` cubre la corrida completa: seguir el plan rinde más valor
y, a la vez, más tiempo de cola.
