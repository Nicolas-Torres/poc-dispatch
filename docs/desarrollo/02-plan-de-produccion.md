# 02 — Plan de producción (etapa LP)

`packages/dispatch-engine/src/dispatch_engine/lp.py` y `production_plan.py`

## Qué se construyó

La etapa 2 resuelta con **OR-Tools / GLOP**: un LP que calcula el flujo ideal `x_r` en cada ruta
(zona de carga → zona de descarga → tipo de flota), siguiendo la formulación de la patente
US 11,187,547:

```
max  c'x
s.a. Hx = b
     x >= 0
```

- **Variables**: `x_r` en toneladas por hora, una por combinación pala × destino compatible × flota.
- **Objetivo**: `c_r` es el valor por tonelada de la pala (`PlanInputs.values_per_tonne`). Por
  defecto 1.0, con lo cual el LP maximiza tonelaje; con valores distintos maximiza valor.
- **Restricciones**:
  - capacidad de excavación de cada pala (cuadrado + carga);
  - bahías de volteo y capacidad de recepción del destino (`DumpZone.capacity_tph`);
  - disponibilidad de flota por tipo, en horas-camión;
  - pisos de producción por pala (`min_rates_tph`, el compromiso de desbroce);
  - ventanas de **blending** por destino y elemento.

La interfaz que ve la etapa 3 (`ProductionPlan`) pasó a tener dos métodos:

```python
def required_rate_tph(shovel_id) -> float      # tasa objetivo
def required_haulage_t(shovel_id) -> float     # toneladas que deben estar comprometidas
```

## Decisiones

**Todas las restricciones se escriben como fracción de un recurso consumida por tonelada-hora.** Una
pala tiene 1.0 hora de pala por hora; un destino tiene `tipping_bays`; una flota tiene `n` camiones.
Así, flotas con payloads distintos y palas con ritmos distintos componen sin casos especiales, y
agregar un recurso nuevo es agregar una fila.

**El requerimiento de acarreo sale de la ley de Little.** Sostener `x` t/h en una ruta cuyo ciclo
dura `c` horas exige `x·c` toneladas en vuelo. De ahí que `required_haulage_t = rate × cycle_time`, y
que Best Path sea **entrada obligatoria** del LP: sin tiempos de ciclo no se puede escribir la
restricción de flota. Esto es exactamente lo que corregía el sesgo documentado en la
[etapa 3](03-asignacion-tiempo-real.md).

**El blending se linealiza sin dividir por el flujo total.** La forma natural
(`Σ g_r x_r / Σ x_r ∈ [min, max]`) no es lineal y además explota si el destino recibe cero. Las
restricciones equivalentes son:

```
Σ (g_r − max) x_r <= 0
Σ (min − g_r) x_r <= 0
```

que además se satisfacen trivialmente cuando el flujo es cero.

**`value_per_tonne` y `min_rate_tph` viven en `PlanInputs`, no en `Shovel`.** No son propiedades del
equipo sino decisiones de planificación; mezclarlas en el dominio habría obligado a reconstruir las
palas para replanificar.

**Sin piso de producción, el plan no mueve estéril.** Con el estéril valiendo menos por tonelada que
el mineral, el LP lo lleva a cero. El escenario de juguete le pone `min_rate_tph=800` a SH03, que es
como se expresa un compromiso de desbroce.

## Resultado en el escenario de juguete

```
  shovel   zone      destination      t/h    cycle   in flight
  SH01     zone_n    crusher          1,610    26.3m       704 t
  SH02     zone_s    crusher            537    28.6m       256 t
  SH03     zone_w    waste_dump         800    27.0m       360 t

  total 2,947 t/h
```

Dos comprobaciones de que el modelo está bien planteado:

- **Ley de la mezcla**: `(1610×0.9 + 537×0.5) / 2147 = 0.80`, exactamente el techo de la ventana
  `[0.6, 0.8]`. El LP se para en el vértice, como corresponde.
- **Toneladas en vuelo**: `704 + 256 + 360 = 1.320 t = 6 camiones × 220 t`. La restricción de flota
  queda justo activa, que es lo esperable cuando la flota es el recurso escaso.

Comparado contra el plan estático, a igual tonelaje total movido (la flota manda), el LP entrega
**3.520 t al chancador contra 2.640 t**: usa las mismas horas-camión en el material que vale más.

## Limitaciones

- **Solo se re-resuelve ante paradas de pala** (ver [06](06-replanificacion.md)). Faltan los otros
  disparadores que menciona la literatura: cambio de material en un banco, camión que entra o sale de
  flota.
- **La literatura describe dos LP débilmente acoplados**; acá hay uno solo.
- **El destino de descarga todavía lo elige la simulación por cercanía**, aunque el LP ya calcula
  flujo por ruta (y por lo tanto por destino). Conectar la decisión de destino al plan es el paso
  natural siguiente, y es lo que permite cumplir blending en la operación real y no solo en el plan.
- **Sin costos de acarreo en el objetivo**: hoy `c_r` depende solo de la pala, no de la ruta. Un
  objetivo más fiel restaría el costo del acarreo, que sí varía por ruta.
- `ortools` emite tres `DeprecationWarning` de sus bindings SWIG al importarse. Son de la librería,
  no del proyecto.

## Verificación

`packages/dispatch-engine/tests/test_lp.py` cubre: que `required_haulage_t` sigue al tiempo de ciclo,
que el tamaño de flota topea el plan, que el valor decide dónde va la flota, que la ventana de
blending obliga a mezclar ambos bancos, que se respetan los pisos de producción y la capacidad del
destino, y que un piso por encima de la capacidad de excavación levanta `InfeasiblePlanError`.

```bash
uv run dispatch-cli plan --scenario toy
uv run dispatch-cli run --scenario toy --hours 2 --plan lp
uv run dispatch-cli run --scenario toy --hours 2 --plan static   # para comparar
```
