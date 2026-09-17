# 04 — Simulación (gemelo digital)

`packages/mine-sim/src/mine_sim/simulation.py` y `scenario.py`

## Qué se construyó

Simulación de eventos discretos con SimPy: un proceso por camión recorriendo la máquina de estados
del ciclo de acarreo, y el motor de asignación invocado en el único punto en que un sistema real
pregunta — cuando el camión queda libre después de descargar.

Ciclo modelado (el de §1.4 de los documentos de contexto):

```
viaje vacío → cola en pala → cuadrado → carga → viaje cargado → cola en descarga → descarga
```

Palas y descargas son `simpy.Resource` (capacidad 1 y `tipping_bays` respectivamente), así que
**las colas emergen de la contención** en vez de estar modeladas explícitamente.

## Decisiones

**`free_at_node` / `free_in_s` describen dónde y cuándo el camión vuelve a estar disponible, no
dónde está físicamente.** Un camión viajando cargado "está", para efectos de despacho, en la
descarga hacia la que va. Sin esta distinción la penalidad de la etapa 3 calcularía el viaje desde
una posición intermedia que no sirve para decidir nada.

**Al salir cargado de la pala, `assigned_shovel` vuelve a `None`.** El camión reingresa a `T'` y deja
de contar en el acarreo comprometido de esa pala en el mismo instante en que deja de alimentarla.
Esto es lo que activa el lookahead multi-camión: cuando un camión pide destino, los que todavía
vienen bajando cargados ya son candidatos y el motor puede reservarles la pala más necesitada.

**Tiempos absolutos en el estado interno, ETA calculada al construir el snapshot.** El runtime guarda
`arrive_at_shovel_s` y `free_at_s` como instantes absolutos; el snapshot los convierte a tiempos
restantes con `max(0, t − ahora)`. La alternativa — ir descontando campos — es incompatible con los
`timeout` atómicos de SimPy.

**Validación del escenario en pydantic.** `ScenarioSpec` verifica que las zonas, descargas y puntos
de partida estén sobre nodos que existen, que cada pala apunte a una zona de carga real y que cada
material tenga al menos un destino que lo acepte. Sin esto, un nodo mal escrito se manifestaba mucho
más tarde como un `NoRouteError` o un `KeyError` sin contexto.

**Pendientes bidireccionales.** Un tramo declarado `bidirectional` genera la arista inversa con la
pendiente negada: la vuelta sube lo que la ida baja.

## Bug encontrado y corregido

El CLI construía la `Simulation` (que creaba su propio `BestPath` y su política por defecto) y
**después sobreescribía `simulation.policy`** para inyectar los parámetros elegidos por el usuario.
Eso dejaba dos objetos `BestPath` vivos — uno con el caché ya tibio y otro sin usar — y un objeto a
medio construir. Se cambió a construir `BestPath` y la política primero y pasarlos explícitamente:
`Simulation(scenario, policy, best_path=best_path)`.

## Simplificaciones

- **El destino de descarga se elige por cercanía**: la descarga compatible más próxima en tiempo
  cargado. En DISPATCH real lo decide el LP, porque el destino forma parte de la definición de ruta
  (y es lo que permite hacer blending). Ver [02](02-plan-de-produccion.md).
- **`free_at_s` de un camión viajando cargado estima llegada + tiempo de descarga**, sin considerar
  una eventual cola en la descarga. Con las descargas holgadas del escenario de juguete el error es
  nulo; con una chancadora saturada sería optimista.
- **Todo es determinista**: no hay fallas de equipo, demoras, cambios de turno ni variabilidad en
  tiempos de carga o viaje. Los códigos de estado (`operativo`, `demora`, `standby`, `malogrado`)
  existen en el dominio pero la simulación solo usa `operativo` y `standby`.
- **Sin congestión en las vías**: los tiempos de viaje no dependen de cuántos camiones circulan.

## Verificación

`packages/mine-sim/tests/test_simulation.py` cubre:

- que un turno completo corre sin trabarse, mueve tonelaje y **ninguna pala queda sin camiones**;
- que el mineral llega a la chancadora y el estéril al botadero (el tonelaje de las palas de mineral
  coincide con el recibido por la chancadora);
- que fijar un camión a una pala lo mantiene ahí durante toda la corrida;
- que el escenario rechaza nodos colgantes y materiales sin destino.
