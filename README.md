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
   varios camiones, pero solo se confirma la asignación del que preguntó. Al terminar de cargar, la
   misma política elige el destino siguiendo el reparto por ruta que calculó el LP — en DISPATCH una
   ruta es zona de carga y zona de descarga juntas.

Sobre eso corre el **gemelo digital**: una simulación de eventos discretos donde palas y descargas
son recursos con capacidad, así que las colas emergen de la contención en vez de estar modeladas.

Las condiciones cambian durante la corrida. Si una pala sale de servicio, el plan se re-resuelve
sobre las que siguen operativas y los camiones que iban hacia ella se redespachan.

## Inicio rápido

Requiere [uv](https://docs.astral.sh/uv/) y Python 3.12.

```bash
uv sync

uv run dispatch-cli scenarios                     # escenarios disponibles
uv run dispatch-cli plan --scenario toy           # etapa 2: el plan de producción
uv run dispatch-cli run --scenario toy --hours 2  # corrida completa con KPIs

uv run dispatch-demo                              # la demo visual 3D en el navegador
```

`dispatch-demo` levanta un servidor local y abre el navegador: cinco demos preparadas, la mina en
3D con los camiones moviéndose sobre sus rutas reales, una línea de tiempo que se puede pausar y
arrastrar, y un tablero de producción que se acumula mientras corre. No hace falta escribir ningún
comando para usarla. Detalle en [`docs/desarrollo/12-demo-visual.md`](docs/desarrollo/12-demo-visual.md).

El plan que resuelve el LP para la mina de ejemplo:

```
  shovel   zone      destination      t/h    cycle   in flight
  SH01     zone_n    crusher          1,327    26.3m       581 t
  SH02     zone_s    crusher            796    28.6m       379 t
  SH03     zone_w    waste_dump         800    27.0m       360 t

  total 2,924 t/h
```

Las toneladas en vuelo suman 1.320 t, o sea los 6 camiones de 220 t: la restricción de flota queda
justo activa. La proporción entre los dos bancos de mineral, 1,67:1, **no** es la que maximizaría el
valor sobre flujos continuos — es la que una flota de camiones enteros puede ejecutar de verdad. El
porqué está en [11](docs/desarrollo/11-plan-ejecutable.md), y es el hallazgo menos intuitivo del
proyecto.

Y la corrida de dos horas sobre esa mina:

```
scenario toy - 2.0 h - lp plan, neediest policy
  tonnes tipped            5,280 t
  in transit                 220 t  (not tipped at cut-off)
  cycles                      24
  avg cycle time            28.0 min
  truck queueing            21.4 min at shovels
  dump queueing              0.0 min
  standby events               0

  route                      tonnes      t/h   plan t/h
  zone_n -> crusher           2,640    1,320      1,327
  zone_s -> crusher           1,760      880        796
  zone_w -> waste_dump          880      440        800

  destination   element   delivered   window
  crusher       cu            0.740   0.60 - 0.80

  shovel   loads    tonnes      t/h   plan t/h   util
  SH01        13     2,860    1,430      1,327     55%
  SH02         8     1,760      880        796     41%
  SH03         4       880      440        800     14%
```

La tabla de mezcla es la que cierra el círculo: el LP promete una ley al chancador y ahí se ve la que
efectivamente llegó — 0,740, dentro de la ventana. Si cayera afuera, la fila lo diría con un
`OUT OF SPEC`.

Se puede comparar contra un plan de tasas fijas con `--plan static`: mueve el mismo tonelaje total
(la flota es el límite en ambos casos) pero entrega 2.640 t al chancador en vez de 3.520 t, porque
gasta las mismas horas-camión en material que vale menos.

### Qué pasa cuando algo se cae

```bash
uv run dispatch-cli run --scenario toy-failure --hours 2
```

Es la misma mina con la pala de alta ley caída 40 minutos. La ventana de mezcla necesita las dos
leyes, así que mientras falta una no hay combinación posible que caiga dentro del rango: el plan
**deja de alimentar al chancador** y manda la flota a estéril hasta que la pala vuelve. El camión
que ya venía en viaje hacia ella se redespacha al llegar al banco.

| | `toy` | `toy-failure` |
|---|---|---|
| Al chancador | 3.520 t | 1.760 t |
| Al botadero | 1.760 t | 2.860 t |
| Replanificaciones | 0 | 2 |

Es una decisión que ningún conjunto de targets fijos habría tomado solo: sale de la estructura del
modelo, no de una regla escrita a mano.

### Por qué el destino tiene que salir del plan

```bash
uv run dispatch-cli run --scenario toy-stockpile --hours 4
```

Este escenario agrega un stockpile arriba de la rampa: acarreo mucho más corto que la chancadora,
pero una tonelada ahí vale menos. Con la chancadora limitada, el plan reparte el banco de alta ley
entre los dos destinos, y la operación sigue ese reparto — la ley entregada queda en 0,761, dentro de
la ventana `[0.60, 0.80]`.

Corriendo lo mismo con `--plan static`, que no tiene opinión sobre destinos y cae a elegir el más
cercano, **todo el mineral termina en el stockpile y la planta queda en cero**. Elegir el destino por
cercanía no es una aproximación algo peor: rompe el objetivo del plan.

### Cuánto compra seguir el plan

```bash
uv run dispatch-cli compare --scenario toy-stockpile --hours 4
```

Corre la misma mina con la misma flota bajo dos estrategias: la de dos etapas y la heurística simple
que la literatura usa como contraste, "**1 camión para n palas**", que manda el camión a la pala donde
podría empezar a cargar antes. Las dos eligen destino de la misma forma, así que lo único que cambia
es la decisión de pala.

```
  metric                    neediest    earliest
  tonnes moved                11,440      11,660
  plan value                  26,972      20,944
  truck queueing (min)          24.9        15.7
  crusher cu                   0.767       0.746
    window               0.60 - 0.80
```

La heurística simple hace exactamente lo que promete: **menos tiempo de cola**, y mueve **más**
toneladas. Pero gana entre 29 % y 30 % menos valor, porque manda los camiones a la pala que los
atiende antes en vez de a la que el plan necesita. Es el resultado que justifica la arquitectura de
dos etapas: **mover más roca y ganar menos plata es un desenlace perfectamente posible**, y es lo que
pasa cuando el despacho optimiza lo que es fácil de medir.

### ¿Y si el mundo no es perfecto?

```bash
uv run dispatch-cli compare --scenario toy-variable --hours 24 --replicas 10
```

Misma mina, pero con dispersión en los tiempos de ciclo y equipos que se rompen solos. La media de
los ciclos no cambia — solo su dispersión — así que cualquier diferencia es atribuible a la varianza.

|  | determinista | con variabilidad |
|---|---|---|
| Ventaja en valor del plan sobre la baseline | +32,9 % | **+22,2 %** |
| Toneladas movidas | 65.560 | 62.766 ± 2.198 |
| Cola de camiones | 33,5 min | **48,3 ± 23,9 min** |
| Ley entregada | 0,754 ± 0 | 0,753 ± 0,001 |
| Ley entregada por la baseline | 0,889 (fuera) | 0,847 ± 0,036 (fuera) |

**La ventaja sobrevive al ruido, aunque se encoge**: de +33 % a +22 %. Parte de lo que la baseline
"gana" bajo ruido lo gana entregando fuera de ley, que es valor que una planta no paga.

Ahí está el resultado más contundente, y no es la media sino la dispersión: seguir el plan entrega
la ley con **±0,001 de desviación contra ±0,036**. Para una concentradora esa varianza es el
problema, no el promedio. Y aparece algo que el gemelo determinista escondía: la varianza sola cuesta
producción con ciclos de media idéntica, y la cola de camiones crece la mitad otra vez.

### Tu propia mina

Los escenarios de arriba vienen incorporados, pero la idea es simular una mina cualquiera. Se exporta
uno que funcione, se edita y se corre:

```bash
uv run dispatch-cli export-scenario --scenario toy --out mi-mina.yaml
uv run dispatch-cli run --scenario mi-mina.yaml --hours 8 --export-events ciclos.csv
```

El archivo declara la red de caminos, las zonas de carga con sus leyes, las palas, los destinos, la
flota, las ventanas de mezcla y las paradas programadas. Pasa por la misma validación que los
escenarios incorporados, así que un nodo colgante o un material sin destino se reportan antes de
empezar a simular, no a mitad de la corrida.

El CSV de eventos lleva el ciclo completo con la razón de cada decisión, que es la base histórica de
la que salen los KPIs de acarreo:

```
time_s,kind,truck_id,shovel_id,dump_id,zone_id,payload_t,detail
0.000,assigned,CAT01,SH01,,,0,"neediest shovel, 704 t behind plan"
```

## Estructura

Workspace de uv con cuatro miembros. La dirección de dependencias es estricta y es lo que mantiene el
motor reusable: **el motor no depende de la simulación**, y la simulación lo consume solo a través
del `Protocol` `DispatchPolicy`.

```
apps/dispatch-cli  ┐
                   ├→  packages/mine-sim  →  packages/dispatch-engine
apps/dispatch-web  ┘
```

| Paquete | Contenido |
|---|---|
| `packages/dispatch-engine` | Dominio (mina, equipos, snapshot) y las tres etapas del motor |
| `packages/mine-sim` | Gemelo digital con SimPy, definición de escenarios y KPIs |
| `apps/dispatch-cli` | Entrypoint de línea de comandos |
| `apps/dispatch-web` | Demo visual 3D: FastAPI y Three.js sin build step |

Los dos puntos de corte que sostienen la arquitectura:

- **`ProductionPlan`** (`required_rate_tph` / `required_haulage_t`): la etapa 3 nunca ve el solver.
  Se puede cambiar `LpProductionPlan` por `StaticProductionPlan` sin tocar la asignación.
- **`DispatchPolicy`**: la simulación no conoce el algoritmo. Sobre esa costura viven hoy la política
  de dos etapas y la baseline de la literatura, y ahí entraría a futuro una por aprendizaje por
  refuerzo.

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
| Escenarios y validación | `pydantic`, `pyyaml` |
| CLI | `typer` |
| Demo visual | `fastapi`, `uvicorn`, `three.js` (vendorizado) |
| Tests / lint | `pytest`, `ruff`, `playwright` |

**Flujo de trabajo**: GitHub Flow, ramas `feature/*` → PR → `main`. Commits con solo el subject line
de Conventional Commits; descripción del PR de máximo 6 líneas.

## Estado

Las tres etapas están implementadas y corren punta a punta, el plan se re-resuelve cuando una pala
sale de servicio, y los destinos siguen el reparto por ruta que calculó el LP. Lo que falta, en orden
de importancia:

- Que la decisión de destino mire la cola en la descarga, no solo la adhesión al plan. Desde que el
  gemelo hace cumplir la capacidad de los destinos, esas colas existen y el efecto es medible.
- Las restricciones operativas de la patente: acarreos cortos, reducción de velocidad y de carga.
- Correlación entre eventos: hoy cada tiempo se sortea independiente, pero la lluvia enlentece todos
  los viajes a la vez y son esos días los que marcan el peor caso.

## Documentación

- [`docs/desarrollo/`](docs/desarrollo/) — bitácora por etapa: qué se construyó, qué se decidió, qué
  se simplificó y qué bugs aparecieron. Incluye el análisis de adhesión al plan y por qué el LP
  corrigió el reparto entre palas.
- [`docs/desarrollo/12-demo-visual.md`](docs/desarrollo/12-demo-visual.md) — por qué Three.js sobre
  FastAPI, cómo se inventan coordenadas para una mina que no las tiene, y los siete hallazgos que
  salieron de iterar con capturas.
- [`docs/contexto/`](docs/contexto/) — cómo funciona el DISPATCH real y qué documentación pública
  existe (papers, patentes, reimplementaciones académicas).
- [`docs/requisitos/`](docs/requisitos/) y [`docs/plan.md`](docs/plan.md) — objetivo y plan de trabajo.
