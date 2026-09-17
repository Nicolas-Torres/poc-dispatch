# 11 — Un plan que la flota pueda ejecutar

`packages/mine-sim/src/mine_sim/scenario.py` (margen de mezcla por defecto)

## Cómo apareció

No salió de leer código ni de un test: salió de correr **todas las combinaciones de escenario y
política** y revisar invariantes. La barrida sobre los 4 escenarios × 3 políticas encontró que las
identidades contables cerraban, que no había standby ni corridas vacías y que el LP resolvía siempre
— pero que **6 de 12 combinaciones entregaban mineral por encima del techo de ley**, incluyendo
`neediest` en **los cuatro** escenarios. No era un caso borde: era el comportamiento por defecto de
la política principal.

## La hipótesis equivocada

La lectura inicial, de la [etapa 02](02-plan-de-produccion.md), era que el plan se para sobre la
restricción y no deja margen, así que un `BlendTarget.margin` daría holgura para el desvío de
ejecución. Barriendo el margen:

| margen | fuera de spec | peor ley |
|---|---|---|
| 0,00 | 6 / 12 | 0,882 |
| 0,03 | 5 / 12 | 0,870 |
| 0,05 | 2 / 12 | 0,833 |
| 0,08 | **3 / 12** | 0,833 |

**No converge.** Con 0,08 empeora respecto de 0,05 y la peor ley no baja de 0,833. Si el margen fuera
holgura contra ruido, más margen tendría que dar menos incumplimientos siempre. No es eso.

## El mecanismo real

Comparando la proporción que pide el plan contra la que entrega la operación:

| | plan SH01:SH02 | real | ley entregada |
|---|---|---|---|
| margen 0,00 | 3,00 | **5,73** | 0,841 ✗ |
| margen 0,05 | 1,67 | **1,62** | 0,748 ✓ |

Con el plan parado sobre el techo, la mezcla óptima pide **3:1**. Con seis camiones, eso deja a SH02
necesitando **1,2 camiones**: una granularidad que la flota no puede representar. Sostiene 1, queda
corta, y la proporción real se va a 5,7:1 — muy por encima del techo.

Con margen 0,05 el plan pide 1,67:1, o sea SH02 necesita 1,7 camiones. Esa proporción **sí** se puede
aproximar con unidades enteras, y la ejecución la sigue casi exactamente.

**El margen no funciona dando holgura: funciona moviendo el plan a una proporción que la flota
discreta puede ejecutar.**

## El resultado que no esperaba

La suposición era que un plan corrido del vértice óptimo costaría valor. Corriendo 24 horas:

| escenario | valor m=0 | valor m=0,05 | delta |
|---|---|---|---|
| toy | 214.940 | 230.560 | **+7,3 %** |
| toy-failure | 209.000 | 229.900 | **+10,0 %** |
| toy-stockpile | 186.780 | 189.244 | +1,3 % |
| toy-variable | 195.067 | 197.267 | +1,1 % |

**El margen no cuesta valor: lo aumenta**, moviendo incluso algo menos de tonelaje. La razón es que
un plan inejecutable no solo incumple la ley — **hace que la flota se derrame a estéril**. Con el plan
pidiendo 3:1, SH02 nunca llega a encabezar el ranking de necesidad, así que el camión que le
correspondía termina en la pala de estéril, que vale 1 en lugar de 3.

Dicho de otra forma: **un plan que la flota puede ejecutar le gana a un plan teóricamente óptimo que
no puede.** El óptimo del LP es óptimo sobre flujos continuos; la operación mueve camiones enteros.

## Qué se cambió

`toy_mine()` ahora declara `margin=0.05` en su ventana de mezcla, y como los otros tres escenarios
derivan de él, lo heredan.

Resultado de la barrida después del cambio: **2 de 12** fuera de especificación, y ninguna es de
`neediest` — las cuatro corridas de la política principal entregan dentro de la ventana (0,748 /
0,751 / 0,767 / 0,738). Las dos que quedan son de `earliest`, que ignora el plan por definición, así
que un margen en el plan no puede ayudarla. Eso es comportamiento correcto, no un defecto pendiente.

## Un test que estaba mal escrito

Al aplicar el cambio falló `test_following_the_plan_beats_the_myopic_baseline_on_value`. La
investigación mostró que `neediest` seguía ganando en todos los horizontes y con ambos márgenes: lo
que fallaba era una afirmación **entre las dos baselines** que yo había puesto de más. A 4 horas
quedan a 1,3 % una de otra y el orden se da vuelta; recién se estabiliza a partir de 12 horas. El
test ahora afirma lo que realmente se sostiene —que seguir el plan le gana a ambas— y deja
explícito por qué no compara las baselines entre sí.

## Limitaciones

- **El 0,05 es empírico para esta mina y esta flota.** El margen que hace ejecutable un plan depende
  del tamaño de flota y de la ventana; no hay todavía una forma de derivarlo.
- Lo correcto sería que **el LP conociera la granularidad de la flota** y no propusiera proporciones
  que requieren fracciones de camión. Eso es un modelo entero-mixto, no un LP, y es un cambio de
  fondo.
- **Los números publicados en los documentos 02, 05, 07, 09 y 10 son anteriores a este cambio.** Son
  una bitácora de lo que se midió en cada etapa, no una descripción del estado actual; el README
  tiene las cifras vigentes.

## Verificación

```bash
uv run dispatch-cli plan --scenario toy      # SH01 1,327 / SH02 796 -> ratio 1.67
uv run dispatch-cli run --scenario toy --hours 2   # ley 0.740, dentro de la ventana
uv run dispatch-cli compare --scenario toy --hours 4
```

La barrida de invariantes que originó todo esto no quedó como script: comprueba, sobre cada escenario
× política, que el tonelaje cargado sea igual al volteado más el que está en tránsito, que el
desglose por destino coincida con el de rutas, que no haya camiones en standby y que la ley entregada
caiga dentro de la ventana. Vale la pena volver a correrla ante cualquier cambio del motor.
