# Bitácora de desarrollo

Un documento por etapa: qué se construyó, qué decisiones se tomaron, qué se simplificó y qué
problemas aparecieron en el camino.

| Documento | Contenido |
|---|---|
| [00 — Workspace y arquitectura](00-workspace-y-arquitectura.md) | Estructura uv, paquetes, dirección de dependencias, tooling |
| [01 — Best Path](01-best-path.md) | Etapa 1: grafo de caminos y rutas de tiempo mínimo |
| [02 — Plan de producción](02-plan-de-produccion.md) | Etapa 2: LP con OR-Tools (capacidades, flota, blending) |
| [03 — Asignación en tiempo real](03-asignacion-tiempo-real.md) | Etapa 3: heurística de pala más necesitada |
| [04 — Simulación](04-simulacion.md) | Gemelo digital con SimPy |
| [05 — CLI y KPIs](05-cli-y-kpis.md) | Entrypoint, log de eventos e indicadores |
| [06 — Replanificación reactiva](06-replanificacion.md) | Paradas de pala, re-solución del LP y redespacho |
| [07 — Destinos que salen del plan](07-destinos-planificados.md) | Reparto de destinos por ruta y ley entregada |
| [08 — Minas en archivo](08-escenarios-en-archivo.md) | Escenarios YAML/JSON y log de ciclos en CSV |
| [09 — Comparación de políticas](09-comparacion-de-politicas.md) | La baseline de la literatura y cuánto compra el plan |

## Estado actual

Las tres etapas del motor están implementadas y corren punta a punta sobre el gemelo digital: el LP
calcula el plan de producción, Best Path los tiempos de viaje, y la asignación en tiempo real reparte
los camiones contra ese plan.

**Lo que ya funciona**

- Etapa 1 (Best Path) con rutas de tiempo mínimo y caché.
- Etapa 2 (LP con OR-Tools/GLOP): capacidades de pala y destino, flota en horas-camión, pisos de
  producción y ventanas de blending.
- Etapa 3 (asignación en tiempo real) en forma reducida, alimentada por el plan.
- Simulación de eventos discretos con colas emergentes en palas y descargas.
- Paradas de pala que disparan la re-solución del LP y el redespacho de camiones.
- Destinos de descarga elegidos siguiendo el reparto por ruta del plan, con la ley entregada medida
  contra la ventana de mezcla.
- Intervención manual del despachador (fijar camión a pala, excluir equipos).
- Minas propias definidas en YAML/JSON y log de ciclos persistido a CSV.
- Una política baseline de la literatura y un comando para comparar estrategias sobre la misma mina.

**Lo que falta**

- Los demás disparadores de replanificación: cambio de material, camión que entra o sale de flota.
- Que la decisión de destino mire la cola en la descarga, no solo la adhesión al plan.
- Restricciones operativas de la patente (acarreos cortos, reducción de velocidad/carga).
- Variabilidad estocástica: hoy las paradas son deterministas y programadas.

## Cómo correrlo

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run dispatch-cli plan --scenario toy                        # el plan de producción (LP)
uv run dispatch-cli run --scenario toy --hours 2               # corrida con plan LP
uv run dispatch-cli run --scenario toy --hours 2 --plan static # comparación con targets fijos
uv run dispatch-cli run --scenario toy-failure --hours 2       # con una pala caída 40 min
uv run dispatch-cli run --scenario toy-stockpile --hours 4     # dos destinos para el mineral

uv run dispatch-cli compare --scenario toy-stockpile --hours 4 # plan vs. heurística simple

uv run dispatch-cli export-scenario --scenario toy --out mi-mina.yaml   # para editar la tuya
uv run dispatch-cli run --scenario mi-mina.yaml --export-events ciclos.csv
```
