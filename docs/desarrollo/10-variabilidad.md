# 10 — Variabilidad estocástica

`packages/mine-sim/src/mine_sim/simulation.py`, `scenario.py`, `planning.py` y `replicas.py`

## Qué se construyó

Hasta acá el gemelo era perfectamente determinista, y eso dejaba una pregunta abierta sobre todo lo
medido antes: **en un mundo sin varianza las colas casi no se forman**, que es justamente el fenómeno
que un sistema de dispatch existe para administrar. El resultado central del proyecto —seguir el plan
rinde ~23 % más valor que la heurística miope— podía ser un artefacto de un mundo perfecto.

- **Dispersión de tiempos de ciclo**: carga, viaje y descarga, con coeficiente de variación por
  escenario.
- **Fallas aleatorias de camión y de pala**, con MTBF y MTTR.
- **`--seed` y `--replicas`**: una corrida es reproducible y los KPIs se reportan como media ±
  dispersión.
- **La flota disponible dispara replanificación**, lo que de paso cierra el pendiente "camión que
  entra o sale de flota".
- Escenario nuevo `toy-variable`: la misma mina con cv de 0,15 / 0,10 / 0,10 y disponibilidad ~95 %
  en camiones y palas.

## Decisiones

**El motor planifica sobre nominales; solo la simulación sortea.** `BestPath` y `load_time_s` siguen
siendo deterministas, y la ETA que el motor registra al despachar es la nominal. El despacho decide
con expectativas y se entera de la desviación cuando el camión llega. Esa asimetría es lo que hace
que el experimento signifique algo: si el motor conociera los tiempos reales no estaríamos midiendo
un sistema de despacho sino un oráculo.

**Lognormal parametrizada para conservar la media.** Activar la variabilidad cambia la dispersión y
**no** el promedio. Sin esa propiedad, cualquier caída de producción sería atribuible a una mina más
lenta y no a la varianza, y el experimento no probaría nada.

**Apagada por defecto.** Con los cv en cero y sin perfiles de confiabilidad, `toy`, `toy-failure` y
`toy-stockpile` producen exactamente los mismos números que antes — verificado con un diff de la
salida contra la rama anterior. Eso evitó reescribir las tablas de nueve documentos, y deja la
comparación determinista contra estocástico como un comando contra otro.

**Un solo mecanismo de parada de pala, dos disparadores.** La falla aleatoria llama al mismo
`_take_shovel_out()` que la parada programada: mismo `_set_shovel_status`, misma prioridad sobre el
recurso, misma replanificación y mismo redespacho. Era el riesgo que la pregunta de alcance marcaba y
se resolvió refactorizando en vez de duplicando.

**Los camiones fallan entre ciclos, no a mitad del viaje.** Misma decisión que en la
[etapa 06](06-replanificacion.md) para las paradas de pala: interrumpir en viaje obliga a modelar la
posición del camión entre dos nodos, que el dominio no representa. Con ciclos de ~27 min y MTBF en
horas, el retardo es chico.

**Un piso de producción que la flota no puede cumplir se afloja, no explota.** Con camiones caídos,
el compromiso de desbroce de 800 t/h puede volverse infactible y el LP tiraba
`InfeasiblePlanError`. Un piso es un compromiso, no una ley física: `solve_scenario_plan` reintenta
sin pisos antes que dejar a la operación sin plan.

**`compare` corre 5 réplicas por defecto y las dos políticas sobre las mismas semillas.** Comparar
bajo ruido con una sola corrida invita a conclusiones que los datos no sostienen, y comparar sobre
mundos distintos mete varianza que no es de las políticas.

## El experimento

24 horas simuladas, la misma mina y la misma flota:

```
DETERMINISTA (toy)                    CON VARIABILIDAD (toy-variable, 10 réplicas)
  metric            neediest earliest   metric              neediest          earliest
  tonnes moved        71,280   71,280   tonnes moved  66,726 +/-2,397   67,342 +/-1,815
  plan value         214,940  175,340   plan value  191,004 +/-18,353 150,700 +/-11,439
  truck queueing        30.2     16.7   truck queueing  144.7 +/-63.4     62.2 +/-25.6
  crusher cu           0.877    0.878   crusher cu     0.840 +/-0.008    0.801 +/-0.045
```

**La ventaja sobrevive y crece: de +22,6 % a +26,7 %.** Las distribuciones no se solapan ni a una
desviación estándar (191.004 − 18.353 = 172.651 contra 150.700 + 11.439 = 162.139), así que no es
ruido. Seguir el plan vale **más** bajo incertidumbre, no menos — que era la hipótesis a favor.

Dos observaciones que el gemelo determinista escondía por completo:

- **La varianza sola cuesta ~6 % de producción**, con tiempos de ciclo de media idéntica. Es el
  resultado clásico de teoría de colas, y era invisible cuando todo era exacto.
- **La cola de camiones casi se quintuplica**, de 30 a 145 minutos. El fenómeno principal que un
  dispatch administra prácticamente no existía en el modelo anterior.

También cambia cómo hay que leer los resultados: la dispersión del valor es de ±18.000 sobre 191.000,
casi un 10 %. Una corrida sola puede estar un 10 % arriba o abajo, que es exactamente el motivo por
el que `compare` dejó de correr una.

## Limitaciones

- **Las fallas son raras en corridas cortas.** Con MTBF realista de 40 h y 60 h, la mayoría de las
  réplicas de 8 h no ve ninguna; el experimento necesita 24 h para que el mecanismo se ejercite. Es
  fiel a la realidad pero hace que el escenario `toy-variable` a pocas horas se parezca demasiado al
  determinista.
- **Sin correlación entre eventos**: cada tiempo se sortea de forma independiente. En una mina real
  la lluvia enlentece todos los viajes a la vez, y esa correlación es la que produce los peores días.
- **El MTBF no depende del uso**: un camión que trabaja el doble no falla el doble.
- **Sin variabilidad en el payload** ni en la ley del material: cada carga es exactamente 220 t de la
  ley nominal del banco.
- **La ley entregada empeora y se vuelve errática** bajo ruido (la baseline da 0,801 ± 0,045, o sea
  réplicas bien afuera de la ventana). El margen de mezcla de la [etapa 02](02-plan-de-produccion.md)
  es la palanca, pero no medimos cuánto margen hace falta para cada nivel de ruido.

## Verificación

```bash
uv run dispatch-cli run --scenario toy-variable --hours 8 --replicas 5
uv run dispatch-cli compare --scenario toy-variable --hours 24 --replicas 10
uv run dispatch-cli run --scenario toy --hours 4    # control: idéntico a antes
```

`packages/mine-sim/tests/test_variability.py` cubre que con la variabilidad apagada la corrida es
idéntica sin importar la semilla, que una misma semilla reproduce exactamente una corrida ruidosa y
semillas distintas no, que el sorteo conserva la media, que un camión caído no recibe asignaciones
hasta ser reparado, que una falla aleatoria de pala replanifica por el mismo camino que una
programada, que achicar la flota disponible achica el plan, que una flota demasiado chica para el
piso de desbroce igual recibe plan, y que la dispersión es cero con una sola réplica.
