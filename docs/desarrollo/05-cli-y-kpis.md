# 05 — CLI y KPIs

`apps/dispatch-cli/src/dispatch_cli/main.py` y `packages/mine-sim/src/mine_sim/events.py`

## Qué se construyó

Un entrypoint que resuelve el plan, corre un escenario y reporta indicadores de acarreo:

```bash
uv run dispatch-cli scenarios                                    # escenarios disponibles
uv run dispatch-cli plan --scenario toy                          # etapa 2: flujo por ruta
uv run dispatch-cli run --scenario toy --hours 2                 # corrida con plan LP
uv run dispatch-cli run --scenario toy --hours 2 --plan static --horizon-min 45
uv run dispatch-cli run --scenario toy --hours 2 --shovel-idle-weight 2
uv run dispatch-cli run --scenario mi-mina.yaml --export-events ciclos.csv
```

Los escenarios propios y la persistencia del log están en [08](08-escenarios-en-archivo.md).

Salida de una corrida de 2 horas sobre la mina de juguete:

```
scenario toy - 2.0 h - lp plan
  tonnes moved         5,280 t  (2,640 t/h)
  cycles                  24
  avg cycle time        27.7 min
  truck queueing        20.0 min at shovels
  dump queueing          0.0 min
  standby events           0

  route                      tonnes      t/h   plan t/h
  zone_n -> crusher           2,640    1,320      1,610
  zone_s -> crusher             880      440        537
  zone_w -> waste_dump        1,760      880        800

  destination   element   delivered   window
  crusher       cu            0.800   0.60 - 0.80

  shovel   loads    tonnes      t/h   plan t/h   util
  SH01        13     2,860    1,430      1,610     55%
  SH02         4       880      440        537     21%
  SH03         9     1,980      990        800     32%
```

## Decisiones

**Los KPIs se derivan del log de eventos, no se calculan dentro de la simulación.** `EventLog`
registra transiciones (`assigned`, `arrive_shovel`, `spot_start`, `load_end`, `dump_end`, …) y
`EventLog.kpis()` los recorre al final. Agregar un indicador nuevo no toca el modelo de simulación,
y es el mismo camino que sigue un dispatch real: la base histórica de ciclos y eventos es la fuente
de los KPIs de acarreo.

**"Pala ocupada" se cuenta desde que el camión se cuadra, no desde que empieza a cargar.** La pala
está tomada durante el cuadrado, así que `engaged = load_end − spot_start`. El ocioso es el resto del
horizonte, que es el número que interesa: tiempo de pala esperando camión.

**Parámetros de las etapas expuestos en la línea de comandos.** `--plan` elige entre el LP y los
targets fijos, `--horizon-min` ajusta la ventana del plan estático y `--shovel-idle-weight` la
penalidad de la etapa 3. Poder comparar dos planes sobre la misma mina y la misma flota, sin tocar
código, es parte de lo que "customizable" significa acá.

**Las tablas muestran plan contra real, a nivel de ruta y de pala.** La columna `plan t/h` sale del
`ProductionPlan`, así que se lee directamente cuánto se está cumpliendo y dónde se está quedando
corto — ver el análisis de adhesión en [03](03-asignacion-tiempo-real.md).

**La ley entregada se reporta contra la ventana con la que se resolvió el plan.** Es el indicador que
cierra el círculo: el LP promete una mezcla, la operación entrega otra, y la diferencia se ve. Se
calcula desde `tonnes_by_route` y las leyes del escenario, no desde el plan — ver
[07](07-destinos-planificados.md).

**`Annotated[...]` para las opciones de typer** en vez de valores por defecto con `typer.Option(...)`,
que ruff marca como llamada en argumento por defecto (B008).

**Un `@app.callback()` explícito** fuerza a typer a tratar `run` como subcomando; con un solo comando
registrado, typer lo habría convertido en el comando raíz y la invocación documentada no
funcionaría.

## Bug encontrado y corregido

La salida usaba un guion largo (`—`) y en consolas Windows (cp1252) se imprimía como `?`. La salida
del CLI quedó restringida a ASCII.

## Consideraciones

- **No hay semilla ni aleatoriedad**: la corrida es determinista, así que dos ejecuciones idénticas
  dan el mismo resultado. Cuando se agregue variabilidad habrá que exponer `--seed` y reportar los
  KPIs sobre varias réplicas.
- El reparto por pala que muestra la tabla **no sigue la proporción de los targets**; la causa está
  documentada en [03](03-asignacion-tiempo-real.md).
