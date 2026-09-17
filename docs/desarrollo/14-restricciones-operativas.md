# Etapa 14 — Restricciones operativas y el despachador en el archivo

`CLAUDE.md` venía diciendo que la etapa 3 estaba "en forma reducida: **sin las restricciones
operativas de la patente**". Era la mayor brecha que quedaba contra el DISPATCH real, y la última de
la lista de pendientes acordada.

La patente US 11,187,547 describe tres, y —esto es lo valioso— describe **por dónde entra cada una al
procedimiento**, no solo qué hace:

| Restricción | Qué altera en el procedimiento |
|---|---|
| Solo acarreos cortos (`SH_PARAM`) | la pertenencia a la lista de candidatos `Tc(s)` |
| Reducción de velocidad | los tiempos de llegada previstos |
| Reducción de carga | los valores de acarreo asignado |

## Por qué importa

Son estados operativos reales. Un camión con la tolva rajada no se va a taller: se carga al 60 %. Uno
con el motor fallando anda más lento. Uno con la transmisión mal solo hace acarreos cortos. **El
despachador no lo saca de servicio, lo sigue usando degradado.**

Hasta acá el motor solo sabía excluirlo (`excluded_trucks`), que es la decisión de martillo. Eso hace
la pregunta medible, y es la hipótesis que justifica la funcionalidad entera: *¿un camión degradado
rinde más que uno estacionado?*

`toy`, 8 h, un camión de seis:

| caso | t | valor | ley | peor adhesión |
|---|---|---|---|---|
| flota sana (referencia) | 21.340 | 62.480 | 0.754 | 93 % |
| **camión excluido** (lo que había antes) | **18.260** | **53.900** | 0.762 | 76 % |
| carga reducida 60 % | 20.196 | 59.356 | 0.753 | 86 % |
| velocidad reducida 70 % | 20.020 | 58.960 | 0.757 | 83 % |
| solo acarreos cortos | 21.120 | 61.820 | 0.757 | 90 % |

Excluir el camión cuesta **14 % del tonelaje y del valor**. Mantenerlo degradado cuesta entre 1 % y
6 %. La restricción vale entre **9 % y 15 % de valor** contra la única alternativa que existía.

## Resultado parcialmente negativo: ¿hace falta que el motor la conozca?

La segunda hipótesis era que modelar la restricción **en el motor** —no solo sufrirla en la física—
mejoraría la adhesión, porque el libro mayor contaría 132 t donde hoy contaría 220. Se midió con un
control: aplicar la restricción en la simulación pero **ocultarla** al motor.

`toy`, 8 h, un camión restringido:

| restricción | motor | t | valor |
|---|---|---|---|
| carga reducida 60 % | la conoce | 20.196 | 59.356 |
| carga reducida 60 % | ciego | 20.064 | 59.048 |
| velocidad reducida 70 % | la conoce | 20.020 | 58.960 |
| velocidad reducida 70 % | ciego | **20.240** | **59.180** |

Media flota al 50 % durante 24 h, que es el caso severo:

| restricción | motor | t | valor |
|---|---|---|---|
| carga reducida 50 % | la conoce | 52.250 | 156.310 |
| carga reducida 50 % | ciego | 52.360 | 156.200 |
| velocidad reducida 50 % | la conoce | 51.920 | **155.100** |
| velocidad reducida 50 % | ciego | 50.820 | 152.240 |

Las tres restricciones no se comportan igual, y conviene decirlo con precisión en vez de declarar
victoria:

- **Acarreos cortos: el motor tiene que conocerla, sin discusión.** No es una degradación, es una
  *restricción*: un motor ciego manda el camión a un acarreo largo, que es exactamente lo que la
  regla existe para impedir. Hay un test binario sobre el log.
- **Velocidad: conocerla compra ~2 %** cuando la degradación es severa. Tiene sentido: la ETA entra
  en la penalidad que decide el emparejamiento camión-pala, así que es una *decisión*, no solo
  contabilidad.
- **Carga: conocerla no compra nada medible** (0,07 % con media flota al 50 %). Y la razón es
  interesante: **el término integral de la [etapa 13](13-seguimiento-del-plan.md) ya absorbe el
  error**. Si un camión entrega 132 t donde el motor esperaba 220, el faltante se acumula y la
  siguiente asignación lo compensa sola. El lazo de realimentación no necesita saber la causa.

Ese último punto es una corroboración indirecta de la etapa 13: el seguimiento integral hace al
sistema robusto justamente a esta clase de error de modelado. La contabilidad de carga reducida se
deja igual —hace que `assigned_haulage_t` diga la verdad, y no cuesta nada— pero **no se le atribuye
un beneficio que la medición no muestra**.

## El despachador, por fin, en el archivo

Verificando lo anterior apareció un hallazgo colateral: `Overrides` —fijar un camión a una pala,
excluir equipos— **existía desde la etapa 03 y no se podía alcanzar sin escribir Python**. El README
decía que la intervención manual viaja en el snapshot y cualquier política la respeta, lo cual era
cierto, pero nadie podía ejercerla desde un escenario.

Se agregó una sección `dispatcher` al `ScenarioSpec`, validada contra la flota como todo lo demás:

```yaml
dispatcher:
  restrictions:
  - truck: CAT01
    load_factor: 0.6
  - truck: CAT02
    speed_factor: 0.7
  - truck: CAT03
    short_hauls_only: true
```

Con eso el requisito de *"usable y customizable en la medida que un sistema de dispatch real lo
permita"* deja de ser una promesa de arquitectura y pasa a ser algo que se puede editar en un archivo.
Un `Overrides` pasado por código sigue ganando, para poder manejar el despachador en vivo.

## Escenario y demo

`toy-restricted`: la misma mina con **media flota dañada pero trabajando** — uno al 60 % de carga,
uno al 70 % de velocidad y uno limitado a acarreos cortos. A 8 h mueve 18.392 t contra 21.340 de la
flota sana, entrega ley 0.764 dentro de ventana y **cero eventos de standby**: nadie queda varado.

Es también la sexta tarjeta de la demo visual, y probablemente la más convincente ante un comité,
porque es la situación en la que una mina está casi siempre: nada roto del todo, todo funcionando a
media máquina.

## Decisiones de diseño

- **Una restricción limita a un camión, no lo deja varado.** El filtro de acarreos cortos siempre
  admite la pala más cercana, por lejos que esté. Si no, un camión restringido en una mina donde todo
  queda lejos se quedaría sin destino legal y el efecto sería peor que excluirlo.
- **`SH_PARAM` es relativo, no absoluto** (por defecto 0,5 del acarreo más largo disponible), que es
  lo que le permite significar lo mismo en un rajo chico y en uno grande. La patente lo describe como
  umbral porcentual.
- **Un camión que viene liviano también se llena más rápido**, así que la carga reducida acorta
  proporcionalmente el tiempo de carga. Si no, la pala quedaría reservada por tiempo que no usa.
- **Todos los valores por defecto son el valor neutro**, así que declarar una restricción vacía es un
  no-op y los escenarios existentes no se mueven un gramo. Hay un test para eso.

## Lo que cambió

| Archivo | Cambio |
|---|---|
| `dispatch_engine/domain/snapshot.py` | `TruckRestriction`, `Overrides.restriction`, `effective_payload_t` |
| `dispatch_engine/policies/common.py` | `dispatchable_candidates` con `SH_PARAM`, ETA con velocidad reducida |
| `dispatch_engine/policies/*.py` | las tres políticas respetan la lista filtrada |
| `mine_sim/scenario.py` | `DispatcherSpec`, `RestrictionSpec`, validación, `toy-restricted` |
| `mine_sim/simulation.py` | aplica velocidad y carga reducidas |
| `apps/dispatch-web/api.py` | sexta demo |
| `packages/mine-sim/tests/test_restrictions.py` | **nuevo**: 12 tests |

## Estado de la etapa 3

Con esto la etapa 3 deja de estar "en forma reducida": el procedimiento de asignación de camiones
vacíos de la patente está completo —listas `Ta(s)` / `T'` / `Tc(s)`, valores de acarreo requerido y
asignado, orden por necesidad, evaluación por penalidad de tiempo ocioso, lookahead sobre varios
camiones confirmando solo el actual, y las tres restricciones operativas.
