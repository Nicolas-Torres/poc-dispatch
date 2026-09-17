# 08 — Minas en archivo y log persistido

`packages/mine-sim/src/mine_sim/scenario.py` (`load_scenario_spec` / `dump_scenario_spec`),
`events.py` (`EventLog.to_csv`) y `apps/dispatch-cli`

## Qué se construyó

El objetivo del proyecto habla de simular **una mina cualquiera**, pero hasta acá solo se podían
correr las minas escritas en Python dentro del paquete: customizar la mina exigía editar código de
librería. Ahora:

- `--scenario` acepta el nombre de un escenario incorporado **o la ruta a un archivo** YAML/JSON.
- `dispatch-cli export-scenario` escribe cualquier escenario a archivo, para arrancar el propio desde
  uno que ya funciona.
- `--export-events` persiste el log de ciclos a CSV.

```bash
uv run dispatch-cli export-scenario --scenario toy --out mi-mina.yaml
# editar mi-mina.yaml
uv run dispatch-cli run --scenario mi-mina.yaml --hours 8 --export-events ciclos.csv
```

## Decisiones

**Una sola opción, no dos.** `--scenario` resuelve primero contra los incorporados y, si no coincide,
lo trata como ruta. Evita el par `--scenario`/`--file` con reglas de precedencia, y el error dice las
dos cosas que pueden estar mal:

```
'noexiste' is neither a built-in scenario (toy, toy-failure, toy-stockpile) nor a file
```

**El archivo pasa por la misma validación que los incorporados.** `ScenarioSpec` ya era un modelo de
pydantic, así que cargar de archivo es `model_validate` sobre el documento: una mina escrita a mano
recibe exactamente los mismos mensajes que una escrita en Python.

**Del error de pydantic solo se muestran los mensajes.** El volcado crudo repite el documento entero
como `input_value`, que en una mina real son cientos de líneas. Lo que sirve es la frase:

```
mi-mina.yaml is not a valid scenario:
  - Value error, load zone 'z' sits on unknown node 'nada'
```

**Se exporta con `exclude_defaults=True`.** El archivo resultante solo trae lo que difiere del
default, así que se lee como una definición de mina y no como un volcado. El round-trip sigue siendo
exacto porque al cargar se rellenan los mismos defaults.

**El formato sale de la extensión** (`.yaml`/`.yml`/`.json`), no de una opción aparte. YAML para
editar a mano, JSON para generar desde otra herramienta.

**El CSV de eventos lleva la razón de cada decisión.** El campo `detail` viaja con el evento, así que
una corrida se puede auditar leyendo el archivo:

```
time_s,kind,truck_id,shovel_id,dump_id,zone_id,payload_t,detail
0.000,assigned,CAT01,SH01,,,0,"neediest shovel, 704 t behind plan"
0.000,assigned,CAT02,SH01,,,0,"neediest shovel, 484 t behind plan"
0.000,assigned,CAT03,SH03,,,0,"neediest shovel, 360 t behind plan"
```

Es la base histórica de ciclos que los documentos de contexto describen como fuente de los KPIs de
acarreo, y el punto de entrada natural para comparar contra datos reales de un dispatch.

## Limitaciones

- **No hay export de KPIs**, solo de eventos. Los indicadores se recalculan desde el CSV o se leen de
  la salida del comando.
- **El CSV se escribe al final**, con todo el log en memoria. Para turnos largos o flotas grandes
  convendría escribir en streaming.
- **No hay `include`/composición entre archivos**: una mina con varias configuraciones de turno se
  copia entera. Los escenarios incorporados sí se derivan entre sí, pero en Python.
- **El formato no está versionado.** Si cambia un campo de `ScenarioSpec`, los archivos viejos dejan
  de validar sin un mensaje que explique la migración.

## Verificación

`packages/mine-sim/tests/test_scenario_files.py` cubre el round-trip en YAML y en JSON (el escenario
cargado es igual al exportado), que una corrida desde archivo da los mismos resultados que el
escenario incorporado, que una extensión desconocida se rechaza, que una mina escrita a mano con un
nodo colgante falla la validación, y que el CSV tiene una fila por evento con su razón.
