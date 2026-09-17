# poc-dispatch

Motor de asignación de camiones para minería a cielo abierto, al estilo de **DISPATCH** (Modular
Mining), corriendo sobre un **gemelo digital** de una mina.

El objetivo no es reimplementar un producto comercial sino tener un sistema de despacho usable y
customizable en la medida en que un dispatch real lo permite, y un simulador donde probarlo. La
lógica que se replica es la pública: los papers de sus creadores, las revisiones académicas y la
patente US 11,187,547 (ver [`docs/contexto/`](docs/contexto/)).

## Cómo funciona

El motor se descompone en las tres etapas que describe la literatura:

```
                 ┌───────────────────────────────────────────────┐
   red de        │  1. BEST PATH                                 │
   caminos ─────►│  grafo dirigido, rutas de tiempo mínimo       │
                 │  se recalcula al cambiar la topología          │
                 └───────────────────┬───────────────────────────┘
                                     │ tiempos de viaje y de ciclo
                                     ▼
                 ┌───────────────────────────────────────────────┐
   capacidades,  │  2. PLAN DE PRODUCCIÓN (LP)                   │
   flota,   ────►│  flujo ideal x_r por ruta, con OR-Tools/GLOP  │
   blending      │  se re-resuelve al cambiar las condiciones     │
                 └───────────────────┬───────────────────────────┘
                                     │ tasa y acarreo requerido por pala
                                     ▼
   un camión     ┌───────────────────────────────────────────────┐
   pide     ────►│  3. ASIGNACIÓN EN TIEMPO REAL                 │
   destino       │  pala más necesitada + camión de menor        │
                 │  penalidad; corre por cada solicitud           │
                 └───────────────────┬───────────────────────────┘
                                     │ destino
                                     ▼
                              camión al banco
```

1. **Best Path** modela la red de caminos como grafo dirigido con pesos (longitud, velocidad,
   pendiente) y resuelve las rutas de **tiempo mínimo**. Vacío y cargado usan ponderaciones
   distintas: la salida del pit no es la inversa de la entrada.
2. **El LP** calcula el flujo ideal en cada ruta *pala → destino → tipo de flota*, sujeto a capacidad
   de excavación, recepción del destino, bahías de volteo, tamaño de flota (en horas-camión), pisos
   de producción y ventanas de **blending**.
3. **La asignación en tiempo real** se dispara cuando un camión queda libre: compara lo comprometido
   contra lo que pide el plan, ordena las palas por necesidad y elige el par camión/pala de menor
   tiempo ocioso total. Es la forma "m camiones para 1 pala" de la literatura: hay lookahead sobre
   varios camiones, pero solo se confirma la asignación del que preguntó.

Sobre eso corre el **gemelo digital**: una simulación de eventos discretos donde palas y descargas
son recursos con capacidad, así que las colas emergen de la contención en vez de estar modeladas.

## Inicio rápido

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.12.

```bash
uv sync

uv run dispatch-cli scenarios                     # escenarios disponibles
uv run dispatch-cli plan --scenario toy           # etapa 2: el plan de producción
uv run dispatch-cli run --scenario toy --hours 2  # corrida completa con KPIs
```

El plan que resuelve el LP para la mina de ejemplo:

```
  shovel   zone      destination      t/h    cycle   in flight
  SH01     zone_n    crusher          1,610    26.3m       704 t
  SH02     zone_s    crusher            537    28.6m       256 t
  SH03     zone_w    waste_dump         800    27.0m       360 t

  total 2,947 t/h
```

La mezcla al chancador da ley 0,80 — el techo exacto de la ventana `[0.6, 0.8]` — y las toneladas en
vuelo suman 1.320 t, o sea los 6 camiones de 220 t: la restricción de flota queda justo activa.

Y la corrida de dos horas sobre esa mina:

```
scenario toy - 2.0 h - lp plan
  tonnes moved         5,280 t  (2,640 t/h)
  cycles                  24
  avg cycle time        27.7 min
  truck queueing        20.0 min at shovels
  dump queueing          0.0 min
  standby events           0

  destination            tonnes
  crusher                 3,520
  waste_dump              1,760

  shovel   loads    tonnes      t/h   plan t/h   util
  SH01        13     2,860    1,430      1,610     55%
  SH02         4       880      440        537     21%
  SH03         9     1,980      990        800     32%
```

Se puede comparar contra un plan de tasas fijas con `--plan static`: mueve el mismo tonelaje total
(la flota es el límite en ambos casos) pero entrega 2.640 t al chancador en vez de 3.520 t, porque
gasta las mismas horas-camión en material que vale menos.

## Estructura

Workspace de uv con tres miembros. La dirección de dependencias es estricta y es lo que mantiene el
motor reusable: **el motor no depende de la simulación**, y la simulación lo consume solo a través
del `Protocol` `DispatchPolicy`.

```
apps/dispatch-cli  →  packages/mine-sim  →  packages/dispatch-engine
```

| Paquete | Contenido |
|---|---|
| `packages/dispatch-engine` | Dominio (mina, equipos, snapshot) y las tres etapas del motor |
| `packages/mine-sim` | Gemelo digital con SimPy, definición de escenarios y KPIs |
| `apps/dispatch-cli` | Entrypoint de línea de comandos |

Los dos puntos de corte que sostienen la arquitectura:

- **`ProductionPlan`** (`required_rate_tph` / `required_haulage_t`): la etapa 3 nunca ve el solver.
  Se puede cambiar `LpProductionPlan` por `StaticProductionPlan` sin tocar la asignación.
- **`DispatchPolicy`**: la simulación no conoce el algoritmo. Se pueden enchufar otras estrategias
  (heurísticas simples, a futuro aprendizaje por refuerzo) sin tocar el simulador.

La intervención manual del despachador —fijar un camión a una pala, excluir equipos, cambiar
prioridades— viaja en el snapshot (`Overrides`), así que cualquier política la respeta sin
reimplementarla.

## Desarrollo

```bash
uv run pytest                              # suite completa
uv run ruff check . && uv run ruff format .
```

| Uso | Librería |
|---|---|
| Grafo y rutas | `networkx` |
| Solver LP | `ortools` (GLOP) |
| Simulación de eventos discretos | `simpy` |
| Escenarios y validación | `pydantic` |
| CLI | `typer` |
| Tests / lint | `pytest`, `ruff` |

**Flujo de trabajo**: GitHub Flow, ramas `feature/*` → PR → `main`. Commits con solo el subject line
de Conventional Commits; descripción del PR de máximo 6 líneas.

## Estado

Las tres etapas están implementadas y corren punta a punta. Lo que falta, en orden de importancia:

- Re-resolver el LP cuando cambian las condiciones (hoy se resuelve una sola vez, al inicio).
- Que el destino de descarga salga del plan y no de la cercanía, que es lo que permite cumplir el
  blending en la operación y no solo en el papel.
- Las restricciones operativas de la patente: acarreos cortos, reducción de velocidad y de carga.
- Fallas, demoras, cambios de turno y variabilidad estocástica.
- Persistir el log de eventos para analizar corridas y comparar políticas.

## Documentación

- [`docs/desarrollo/`](docs/desarrollo/) — bitácora por etapa: qué se construyó, qué se decidió, qué
  se simplificó y qué bugs aparecieron. Incluye el análisis de adhesión al plan y por qué el LP
  corrigió el reparto entre palas.
- [`docs/contexto/`](docs/contexto/) — cómo funciona el DISPATCH real y qué documentación pública
  existe (papers, patentes, reimplementaciones académicas).
- [`docs/requisitos/`](docs/requisitos/) y [`docs/plan.md`](docs/plan.md) — objetivo y plan de trabajo.
