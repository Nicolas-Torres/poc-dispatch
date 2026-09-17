# 01 — Best Path

`packages/dispatch-engine/src/dispatch_engine/best_path.py`

## Qué se construyó

La etapa 1 del motor, implementada de verdad: la red de caminos como grafo dirigido con pesos y
rutas de **tiempo mínimo** entre cualquier par de puntos, cacheadas.

- `RoadNetwork` (`domain/mine.py`) es una tupla inmutable de `Edge`, cada una con longitud,
  velocidad límite y pendiente.
- `Edge.travel_time_s(loaded=...)` deriva el tiempo de viaje del tramo.
- `BestPath` construye **dos** grafos de networkx (vacío y cargado) y resuelve con Dijkstra.
- `BestPath.warm(mine)` precalcula todos los pares zona de carga ↔ zona de descarga, como hace el
  sistema real cuando cambia la red.

## Decisiones

**Dos ponderaciones, no una.** El camión cargado es más lento, y la penalidad de pendiente lo afecta
justo en la subida de salida del pit. La consecuencia es que la ruta óptima de salida no es
necesariamente la inversa de la de entrada, así que mantener un solo grafo habría sido incorrecto.

**Modelo de velocidad efectiva** (`domain/mine.py`):

```
velocidad = límite
            × 0.7                     si va cargado
            × (1 − 0.06 × pendiente)  si la pendiente es positiva, con piso de 0.25
```

La asimetría es deliberada: **subir penaliza, bajar no bonifica**. Un camión en bajada está limitado
por freno y retardador, no por potencia disponible; darle un premio por bajar habría producido rutas
irreales que buscan descensos.

**networkx en vez de un Dijkstra propio.** Son ~30 líneas de algoritmo, pero networkx ya trae A*,
flujos y utilidades de grafos que van a servir cuando aparezcan pesos dinámicos por tráfico.

**Aristas paralelas colapsan a la más rápida.** `DiGraph` no admite multi-arista; si el escenario
define dos tramos entre los mismos nodos, se conserva el de menor tiempo para esa condición de
acarreo. Si en algún momento hacen falta vías paralelas con capacidad propia, habrá que pasar a
`MultiDiGraph`.

**Caché invalidada solo al cambiar la red.** `route()` cachea por `(origen, destino, cargado)` y el
caché se limpia únicamente en `update_network()`. Esto replica el comportamiento real: Best Path se
re-corre ante un cierre de vía o una rampa nueva, no ante cada solicitud de camión.

## Consideraciones y limitaciones

- `RoadNetwork.version` existe como marcador semántico del cambio de topología, pero como la
  estructura es inmutable, la invalidación efectiva la produce `update_network()`. Si más adelante
  la red se muta en vivo, ese campo pasa a ser el disparador real.
- **Los pesos son estáticos.** La patente original describe pesos que dependen también del tráfico
  acumulado en el tramo. Hoy no hay congestión modelada: dos camiones en la misma rampa viajan igual
  de rápido que uno solo.
- La pendiente se declara por tramo en el sentido de avance; `ScenarioSpec.build()` genera la arista
  inversa con la pendiente negada cuando el tramo es bidireccional.

## Verificación

`packages/dispatch-engine/tests/test_best_path.py` cubre:

- que la ruta elegida minimiza tiempo y no distancia (un camino directo largo y lento pierde contra
  uno igual de largo pero rápido);
- que el acarreo cargado es más lento que el vacío;
- que la subida penaliza y la bajada no bonifica;
- que el caché se reutiliza y se limpia al cambiar la red;
- que un destino inalcanzable o inexistente levanta `NoRouteError`.
