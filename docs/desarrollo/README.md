# Bitácora de desarrollo

Un documento por etapa: qué se construyó, qué decisiones se tomaron, qué se simplificó y qué
problemas aparecieron en el camino.

| Documento | Contenido |
|---|---|
| [00 — Workspace y arquitectura](00-workspace-y-arquitectura.md) | Estructura uv, paquetes, dirección de dependencias, tooling |
| [01 — Best Path](01-best-path.md) | Etapa 1: grafo de caminos y rutas de tiempo mínimo |
| [02 — Plan de producción](02-plan-de-produccion.md) | Etapa 2: interfaz del LP (todavía sin solver) |
| [03 — Asignación en tiempo real](03-asignacion-tiempo-real.md) | Etapa 3: heurística de pala más necesitada |
| [04 — Simulación](04-simulacion.md) | Gemelo digital con SimPy |
| [05 — CLI y KPIs](05-cli-y-kpis.md) | Entrypoint, log de eventos e indicadores |

## Estado actual

Esqueleto punta a punta funcionando: una mina de juguete corre un turno simulado, el motor asigna
camiones a palas usando Best Path real y una heurística de necesidad, y salen KPIs de acarreo.

**Lo que ya funciona**

- Etapa 1 (Best Path) implementada.
- Etapa 3 (asignación en tiempo real) implementada en forma reducida.
- Simulación de eventos discretos con colas emergentes en palas y descargas.
- Intervención manual del despachador (fijar camión a pala, excluir equipos).

**Lo que falta**

- Etapa 2 (LP con OR-Tools): hoy las tasas requeridas son fijas, definidas en el escenario.
- Restricciones operativas de la patente (acarreos cortos, reducción de velocidad/carga).
- Fallas, demoras, cambios de turno y variabilidad estocástica.
- Persistencia del log de eventos.

## Cómo correrlo

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run dispatch-cli run --scenario toy --hours 2
```
