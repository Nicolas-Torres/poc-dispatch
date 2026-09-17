# 06 — Replanificación reactiva

`packages/mine-sim/src/mine_sim/simulation.py`, `scenario.py` y
`packages/dispatch-engine/src/dispatch_engine/domain/snapshot.py`

## Qué se construyó

Hasta acá el plan se resolvía una vez y no cambiaba nunca. Ahora las condiciones cambian durante la
corrida y el sistema reacciona, que es lo que describe la literatura: *"el LP se re-resuelve cuando
cambian las condiciones: una pala entra en demora o falla, cambia el material, o se agrega o retira
equipo"*.

- **Estado de pala en el snapshot.** `ShovelStatus` envuelve la pala con su código de estado
  (`operativo`, `demora`, `standby`, `malogrado`), igual que `TruckStatus` para los camiones.
  `MineSnapshot.available_shovels()` es ahora el único lugar donde se decide si una pala puede
  recibir camiones, combinando estado y exclusión manual del despachador.
- **Disrupciones programadas.** El escenario declara paradas: `DisruptionSpec(shovel, start_min,
  duration_min, status)`.
- **Disparador de replanificación.** Cuando una pala cambia de estado, el LP se re-resuelve sobre las
  palas que siguen operativas y se reconstruye la política con el plan nuevo.
- **Redespacho.** Un camión que llega a una pala que quedó fuera de servicio no se queda esperando:
  pide destino de nuevo desde el banco.
- Escenario nuevo `toy-failure`: la misma mina con SH01 caída 40 minutos desde el minuto 30.

## Decisiones

**Dos costuras: `PlanProvider` y `PolicyFactory`.** La simulación recibe una función que resuelve el
plan dada la lista de palas fuera de servicio, y otra que construye la política dado un plan. Cuando
algo cambia, la simulación vuelve a llamar a ambas. Alternativa descartada: hacer mutable el plan
dentro de la política. La política es un `dataclass(frozen=True)` a propósito — es una regla de
decisión, no un objeto con ciclo de vida — y reconstruirla es barato.

El efecto secundario útil es que el plan estático también pasa por la misma costura: su provider
ignora el argumento y devuelve siempre los mismos targets. Así, comparar "plan que reacciona" contra
"plan que no reacciona" no necesita dos caminos de código.

**Primero se reporta el estado, después la pala se detiene físicamente.** Al minuto de la falla se
marca el estado y se replanifica, con lo cual dejan de mandarse camiones ahí de inmediato; recién
después el proceso de la parada toma el recurso de la pala. Si se hiciera al revés, se seguirían
despachando camiones a una pala que ya está por detenerse.

**La parada usa `PriorityResource` con prioridad por debajo de los camiones.** Así la pala se detiene
apenas termina la carga que tiene encima, en vez de tener que atender primero a toda la cola. Sin la
prioridad, una cola de cuatro camiones retrasaría la falla veinte minutos.

**Los camiones en viaje no se interrumpen.** Terminan el viaje y piden destino al llegar. Es la
decisión con más discusión:

- Interrumpir a mitad de camino obliga a modelar la posición del camión *entre* dos nodos, que el
  dominio hoy no representa — habría que inventar un nodo intermedio o hacer que el camión
  retroceda.
- Dejarlo llegar es además razonable en la realidad: el camión ya está en el pit y los bancos están
  cerca entre sí, así que el redespacho desde el banco cuesta poco.

Queda registrado en el log como `reassigned`, así que el costo de la decisión es medible.

## Resultado

Corrida de 2 horas, la misma mina con y sin la falla de SH01:

| | `toy` | `toy-failure` |
|---|---|---|
| Movido | 5.280 t | 4.620 t |
| Al chancador | 3.520 t | 1.760 t |
| Al botadero | 1.760 t | 2.860 t |
| Replanificaciones | 0 | 2 |
| Reasignaciones | 0 | 1 |

**Lo interesante es a dónde va la flota.** Con SH01 fuera, la ventana de mezcla `[0.6, 0.8]` deja de
ser alcanzable: el único mineral disponible es el de ley 0,5 y ninguna combinación de lo que queda
cae dentro del rango. El LP entonces **deja de alimentar al chancador** y manda la flota a estéril
hasta que SH01 vuelve. Es una decisión que ningún conjunto de targets fijos habría tomado solo, y
sale de la estructura del modelo, no de una regla escrita a mano.

Las dos replanificaciones son la caída y la vuelta.

## Limitaciones

- **Solo hay disrupciones de palas.** Camiones que fallan o salen de flota cambiarían la restricción
  de flota del LP, y el modelo ya lo soporta (`FleetType.trucks`), pero falta el evento.
- **Las paradas son deterministas y programadas.** No hay MTBF ni variabilidad; es lo que hace que
  las corridas sean reproducibles, pero no permite estudiar disponibilidad.
- **Los otros disparadores de la literatura no están**: cambio de material en un banco, camión que
  entra o sale, cambio de prioridades en caliente.
- **La columna `plan t/h` del reporte muestra el plan final**, no un promedio ponderado por el tiempo
  que cada plan estuvo vigente. En `toy-failure` eso significa comparar el real de dos horas contra
  el plan de después de la recuperación.
- El estado `delay` y `standby` se aceptan en el escenario y se tratan igual que `down`: la pala no
  recibe camiones. La diferencia entre ellos hoy es solo de registro.

## Verificación

```bash
uv run dispatch-cli run --scenario toy-failure --hours 2
```

`packages/mine-sim/tests/test_simulation.py` cubre que la caída y la vuelta disparan exactamente dos
replanificaciones, que con la pala caída la flota mueve más estéril que mineral, que **ningún camión
es atendido por la pala mientras está fuera de servicio**, y que el camión que ya venía en viaje se
redespacha sin pasar por standby. `packages/dispatch-engine/tests/test_neediest_shovel.py` cubre que
una pala fuera de servicio no recibe asignaciones y que, si todas lo están, la política devuelve
`None` en vez de inventar un destino.
