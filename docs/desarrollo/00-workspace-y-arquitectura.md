# 00 — Workspace y arquitectura

## Qué se construyó

Workspace de uv con tres miembros y una raíz virtual (no empaquetable):

```
poc-dispatch/
├── pyproject.toml              # raíz del workspace, dependencias de desarrollo, config de ruff/pytest
├── packages/dispatch-engine/   # dominio + las 3 etapas del motor
├── packages/mine-sim/          # gemelo digital (SimPy) + escenarios + KPIs
└── apps/dispatch-cli/          # entrypoint de línea de comandos
```

Dirección de dependencias:

```
apps/dispatch-cli  →  packages/mine-sim  →  packages/dispatch-engine
```

## Decisiones

**Workspace en vez de paquete único.** El objetivo pide un sistema customizable; separar el motor de
la simulación obliga a que el motor no pueda "espiar" el estado interno del simulador y deba
trabajar solo con el snapshot que recibe. Esa restricción es justamente lo que permitirá después
conectar el motor a datos reales en lugar de simulados.

**El motor no depende de la simulación.** La simulación conoce el `Protocol` `DispatchPolicy`
(`dispatch_engine/policy.py`) y nada más del algoritmo. Cambiar de política (heurística simple, LP
completo, a futuro aprendizaje por refuerzo) no toca el simulador.

**El dominio vive dentro de `dispatch-engine`, no en un paquete aparte.** Los tipos del dominio
(`Mine`, `Truck`, `Shovel`, `MineSnapshot`) están fuertemente acoplados a la lógica del motor: un
cuarto paquete solo para ellos habría agregado ceremonia sin aislar nada.

**pydantic solo en `mine-sim`.** La validación paga donde hay entrada del usuario (la definición del
escenario). El motor usa `dataclass(frozen=True, slots=True)`, que es más liviano y no arrastra
dependencias a lo que a futuro podría ser una librería reutilizable.

**`ortools` declarado desde ahora**, aunque la etapa LP todavía no exista, para no repetir la
resolución de dependencias en el próximo hito. Arrastra `numpy`, `pandas` y `protobuf`, así que el
`uv sync` inicial es pesado; si molesta, moverlo a un extra opcional de `dispatch-engine`.

**Tooling:** `ruff` para lint y formato (línea de 100), `pytest` con `testpaths = ["packages", "apps"]`.
Python 3.12 fijado en `.python-version`, que es donde OR-Tools tiene wheels estables.

## Consideraciones

- Cada paquete declara `[tool.hatch.build.targets.wheel] packages = ["src/<nombre>"]` explícitamente.
  Hatchling suele inferirlo, pero dejarlo escrito evita fallos silenciosos de empaquetado cuando el
  nombre del proyecto (con guion) no coincide con el del módulo (con guion bajo).
- Si hay otro proyecto Python activo en la terminal, `uv run` avisa que ignora `VIRTUAL_ENV` y usa
  el `.venv` del workspace. Es solo un aviso, el entorno que usa es el correcto.

## Verificación

```bash
uv sync           # resuelve los tres paquetes como editables del workspace
uv run pytest     # 19 tests
uv run ruff check .
```
