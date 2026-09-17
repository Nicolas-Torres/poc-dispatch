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
- Intervención manual del despachador (fijar camión a pala, excluir equipos).

**Lo que falta**

- Re-resolver el LP cuando cambian las condiciones (hoy se resuelve una sola vez, al inicio).
- Que el destino de descarga salga del plan y no de la cercanía.
- Restricciones operativas de la patente (acarreos cortos, reducción de velocidad/carga).
- Fallas, demoras, cambios de turno y variabilidad estocástica.
- Persistencia del log de eventos.

## Cómo correrlo

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run dispatch-cli plan --scenario toy                        # el plan de producción (LP)
uv run dispatch-cli run --scenario toy --hours 2               # corrida con plan LP
uv run dispatch-cli run --scenario toy --hours 2 --plan static # comparación con targets fijos
```
