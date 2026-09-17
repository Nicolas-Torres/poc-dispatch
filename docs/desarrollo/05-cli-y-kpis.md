# 05 — CLI y KPIs

`apps/dispatch-cli/src/dispatch_cli/main.py` y `packages/mine-sim/src/mine_sim/events.py`

## Qué se construyó

Un entrypoint que corre un escenario y reporta indicadores de acarreo:

```bash
uv run dispatch-cli run --scenario toy --hours 2
uv run dispatch-cli run --scenario toy --hours 2 --horizon-min 45 --shovel-idle-weight 2
uv run dispatch-cli scenarios
```

Salida de una corrida de 2 horas sobre la mina de juguete:

```
scenario toy - 2.0 h simulated
  tonnes moved         5,280 t  (2,640 t/h)
  cycles                  24
  avg cycle time        27.8 min
  truck queueing        20.8 min at shovels
  dump queueing          0.0 min
  standby events           0

  destination            tonnes
  crusher                 2,640
  waste_dump              2,640

  shovel   loads    tonnes   engaged   idle    util
  SH01         9     1,980     45.6m   74.4m     38%
  SH02         4       880     24.7m   95.3m     21%
  SH03        13     2,860     56.3m   63.7m     47%
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

**Parámetros de la política expuestos en la línea de comandos.** `--horizon-min` y
`--shovel-idle-weight` permiten ver el efecto de las dos palancas de la etapa 3 sin tocar código,
que es parte de lo que "customizable" significa acá.

**`Annotated[...]` para las opciones de typer** en vez de valores por defecto con `typer.Option(...)`,
que ruff marca como llamada en argumento por defecto (B008).

**Un `@app.callback()` explícito** fuerza a typer a tratar `run` como subcomando; con un solo comando
registrado, typer lo habría convertido en el comando raíz y la invocación documentada no
funcionaría.

## Bug encontrado y corregido

La salida usaba un guion largo (`—`) y en consolas Windows (cp1252) se imprimía como `?`. La salida
del CLI quedó restringida a ASCII.

## Consideraciones

- **El log de eventos solo vive en memoria.** Para analizar corridas o comparar políticas hará falta
  persistirlo (CSV o parquet) — es además la puerta de entrada natural para comparar contra datos
  reales de un dispatch.
- **No hay semilla ni aleatoriedad**: la corrida es determinista, así que dos ejecuciones idénticas
  dan el mismo resultado. Cuando se agregue variabilidad habrá que exponer `--seed` y reportar los
  KPIs sobre varias réplicas.
- El reparto por pala que muestra la tabla **no sigue la proporción de los targets**; la causa está
  documentada en [03](03-asignacion-tiempo-real.md).
