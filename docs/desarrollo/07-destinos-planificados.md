# 07 — Destinos que salen del plan

`packages/dispatch-engine/src/dispatch_engine/policies/neediest_shovel.py` (`choose_destination`),
`domain/snapshot.py` y `policy.py`

## Qué se construyó

Era la deuda marcada desde la [etapa 2](02-plan-de-produccion.md): el LP calculaba flujo **por ruta**
—zona de carga *y* zona de descarga— pero la simulación elegía el destino por cercanía, así que el
blending se cumplía en el papel y no en la operación.

Ahora la decisión de destino es del motor y sigue el plan:

- `DispatchPolicy` tiene un segundo método, `choose_destination(snapshot, truck_id, load_zone_id)`.
- `ProductionPlan` expone `destination_rates_tph(load_zone_id)`: cómo debería repartirse la salida de
  esa zona entre destinos. El plan estático devuelve `{}` — no tiene opinión — y ahí la política cae
  al destino compatible más cercano, que es el comportamiento anterior.
- El snapshot lleva `delivered_t` por ruta y cada camión cargado declara `origin_zone` y
  `assigned_dump`, así que `committed_to_route()` suma lo ya volteado más lo que viene en camino.
- El objetivo del LP distingue destinos: `c_r = valor de la pala × valor del destino`. La misma
  tonelada de mineral vale menos en un stockpile que pasando por la chancadora.
- Escenario nuevo `toy-stockpile`: un stockpile arriba de la rampa (acarreo corto, valor menor) y la
  chancadora limitada a 1.400 t/h, para que el plan tenga que repartir de verdad.

## Decisiones

**La misma política decide las dos patas.** En DISPATCH una ruta *es* zona de carga más zona de
descarga; una política que decide dónde cargar pero no dónde voltear está a medias. El costo es que
cualquier política futura debe implementar los dos métodos, y parece el precio correcto.

**El destino se mide contra el acumulado; la pala, contra lo comprometido en el instante.** Es una
asimetría deliberada:

- Mandar un camión a una pala es una decisión sobre *tenerla alimentada ahora*: lo que importa es
  quién viene en camino.
- Repartir destinos es una decisión sobre *una mezcla que se promedia a lo largo del turno*: lo que
  importa es el acumulado, porque la ley que recibe la planta es un promedio corrido.

Si el destino se decidiera solo con lo que está en vuelo, con seis camiones y cargas de 220 t el
reparto sería demasiado grueso y oscilaría.

**`delivered_t` vive en el snapshot, no en la política.** Mantiene la política como función pura del
snapshot, que es lo que la hace testeable sin simulador. Y no es un artificio: la producción
acumulada del turno es exactamente lo que un dispatch real lleva contra el plan.

**`{}` significa "el plan no opina".** Permite que el plan estático siga funcionando sin inventar
datos de destino que no tiene, y deja el fallback por cercanía como un camino explícito y probado en
vez de un caso especial escondido.

## Resultado

Corrida de 4 horas sobre `toy-stockpile`, con el plan LP:

```
  route                      tonnes      t/h   plan t/h
  zone_n -> crusher           3,740      935      1,050
  zone_n -> stockpile         2,860      715        842
  zone_s -> crusher           1,540      385        350
  zone_w -> waste_dump        2,860      715        800

  destination   element   delivered   window
  crusher       cu            0.783   0.60 - 0.80
```

El banco de alta ley se reparte 57 % / 43 % entre chancadora y stockpile contra un plan de
56 % / 44 %, y **la ley entregada a la chancadora queda dentro de la ventana**.

La comparación que justifica la etapa es correr lo mismo con `--plan static`, que es el fallback por
cercanía:

```
  route                      tonnes      t/h   plan t/h
  zone_n -> stockpile         3,960      990          0
  zone_s -> stockpile         1,980      495          0
  zone_w -> waste_dump        5,720    1,430          0
```

El stockpile está más cerca que la chancadora, así que **todo el mineral termina ahí y la planta
queda en cero**. No hay fila de mezcla porque no llegó material. Elegir el destino por cercanía no es
una aproximación algo peor: rompe el objetivo del plan.

## Limitaciones

- **La ley se sigue, no se garantiza.** La política persigue el reparto planificado, pero si la etapa
  3 entrega menos mineral de alta ley del que el plan pedía, la mezcla se corre. En la corrida de
  arriba quedó en 0,783 contra 0,80 del plan: dentro de la ventana, usando el margen que la ventana
  da. Con una ventana angosta y mala adhesión al plan, podría salirse.
- **El grano es grueso.** Cada decisión mueve 220 t; un reparto 56/44 se aproxima por acumulación, no
  se cumple carga a carga.
- **El destino no mira la cola.** Se elige por adhesión al plan, sin considerar cuántos camiones hay
  esperando para voltear. Con una chancadora saturada convendría desviar al stockpile aunque el plan
  no lo pida.
- **No hay re-ruteo en viaje**: si la chancadora cae mientras el camión va hacia ella, el camión
  llega igual.
- El acumulado `delivered_t` **nunca se reinicia**: es acumulado de corrida, no de turno. Cuando
  existan turnos habrá que decidir si el reparto se persigue por turno o por día.

## Verificación

```bash
uv run dispatch-cli plan --scenario toy-stockpile
uv run dispatch-cli run --scenario toy-stockpile --hours 4
uv run dispatch-cli run --scenario toy-stockpile --hours 4 --plan static   # el fallback
```

`packages/dispatch-engine/tests/test_destinations.py` cubre que el reparto sigue la proporción
planificada (seis cargas a la planta y dos al stockpile con un plan 75/25), que las cargas en vuelo
cuentan para el reparto, que sin plan de rutas cae al destino más cercano, y que nunca se manda
material a un destino que no lo acepta. `packages/mine-sim/tests/test_simulation.py` cubre la corrida
completa: con plan LP la chancadora recibe material dentro de la ventana de ley, y sin plan de rutas
todo el mineral toma el acarreo más corto.
