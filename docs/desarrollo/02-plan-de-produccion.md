# 02 — Plan de producción (etapa LP)

`packages/dispatch-engine/src/dispatch_engine/production_plan.py`

## Qué se construyó

**Solo la interfaz.** El solver no está implementado todavía; esta etapa se dejó definida para que la
etapa 3 se escribiera contra ella desde el principio.

```python
class ProductionPlan(Protocol):
    def required_rate_tph(self, shovel_id: ShovelId) -> float: ...
```

La implementación actual es `StaticProductionPlan`, que devuelve tasas fijas tomadas del escenario
(`ShovelSpec.target_rate_tph`).

## Decisiones

**Definir la interfaz antes que el solver.** La etapa 3 ya consume `ProductionPlan` y nunca toca un
`StaticProductionPlan` concreto, así que cuando entre `LpProductionPlan` con OR-Tools/GLOP la
asignación en tiempo real no se modifica. Ese es el punto de corte que la literatura describe: el LP
corre a intervalos, la asignación corre por camión, y lo único que cruza entre ambos son las tasas.

**OR-Tools ya está instalado** (GLOP disponible) aunque no se use, para que el próximo hito sea solo
escribir el modelo.

## Consideraciones para cuando se implemente el LP

**La "ruta" del LP todavía no existe como tipo.** En DISPATCH una ruta es *zona de carga + zona de
descarga + camino + registro de ley + tipo de vehículo*, y el LP resuelve el flujo `x_r` por ruta. Lo
que hoy se llama `Route` (`domain/routing.py`) es apenas un camino sobre el grafo. Al implementar el
LP habrá que introducir ese concepto — probablemente `PlanRoute` — y la interfaz pasará de "tasa por
pala" a "tasa por ruta", que es lo que además permite decidir el destino de descarga (hoy resuelto
por cercanía, ver [04](04-simulacion.md)).

**El requerimiento de camiones debería derivarse del tiempo de ciclo.** La etapa 3 hoy convierte la
tasa en toneladas usando una ventana fija (ver [03](03-asignacion-tiempo-real.md)). La formulación
correcta relaciona flujo y tiempo de ciclo: la suma de `flujo × tiempo de ciclo` está limitada por la
flota disponible. Ese es el término que corrige el sesgo documentado en la etapa 3.

**Restricciones a modelar**, según los documentos de contexto: capacidad de excavación por pala,
capacidad de recepción por destino, conservación de flujo, blending (leyes dentro de rango en la
chancadora), prioridades entre palas y tamaño de flota por tipo.

## Verificación

No hay tests propios de esta etapa todavía: `StaticProductionPlan` es una búsqueda en un diccionario
y se ejercita indirectamente en los tests de la etapa 3.
