# Etapa 12 — La demo visual 3D

El objetivo de esta etapa está en `docs/requisitos/objetivo.md`: una aplicación visual interactiva
donde se vea la mina funcionando —palas, camiones en movimiento y un tablero de productividad—, en
3D, que un usuario pueda correr sin escribir comandos y que sea demostrable ante un comité.

## Evaluación de tecnología

La decisión no era obvia, así que se evaluaron tres caminos:

| Opción | A favor | En contra | Veredicto |
|---|---|---|---|
| **Three.js + FastAPI** | 3D real en el navegador, sin instalar nada; el motor queda intacto detrás de una API | Hay que inventar coordenadas, que el dominio no tiene | **Elegida** |
| Dash / Streamlit en Python | Cero JavaScript, tablero casi gratis | El 3D es un gráfico, no una escena: no hay cámara ni movimiento continuo | Descartada |
| Escritorio (PyVista, Panda3D) | 3D potente | Obliga a instalar; imposible de mostrar en una laptop ajena | Descartada |

Sobre Three.js, el requisito explícito del objetivo: es la única de las tres que da una escena 3D
navegable con cámara orbital y animación por cuadro dentro de un navegador. Se usa **sin build step**
—un `importmap` y módulos ES nativos— y con la librería **vendorizada en `static/vendor/`**. Eso
último es deliberado: una demo ante un comité no puede depender de que haya wifi ni de que un CDN
responda.

## Arquitectura

La regla fue **no tocar el motor**. `dispatch-web` es un consumidor más, al mismo nivel que
`dispatch-cli`, y depende de `dispatch-engine` y `mine-sim` sin que ninguno de los dos sepa que
existe.

```
navegador (Three.js)  ←  JSON  ←  dispatch-web (FastAPI)  →  mine-sim  →  dispatch-engine
```

La simulación **no** corre en tiempo real: el backend la corre entera, de punta a punta, y devuelve
un guion completo que el navegador reproduce. Eso permite pausar, arrastrar la línea de tiempo y
volver atrás, que es lo que hace falta para explicarle algo a alguien; una simulación en vivo solo
deja mirar.

### `geometry.py` — inventar coordenadas honestas

El dominio no tiene coordenadas: los nodos son nombres. Pero sí tiene dos cosas con las que se pueden
deducir, y el punto era que la imagen no fuera decorativa:

- **Planta**: *multidimensional scaling* clásico sobre las distancias de camino más corto, resuelto
  con una descomposición en autovalores de numpy. Doce líneas, determinista, y la salida ya está en
  metros. Un test verifica que la correlación entre distancia dibujada y distancia real supere 0.9.
- **Elevación**: se integra la pendiente de cada segmento por BFS desde una zona de descarga, que es
  el datum natural. Como `ScenarioSpec.build` emite el segmento inverso con el signo cambiado, el
  campo de elevación resulta consistente: un test lo comprueba segmento por segmento. En `toy` el pit
  queda 150 m por debajo del chancador, que es lo que dicen las rampas.

### `replay.py` — de log de eventos a guion

El log dice *cuándo* pasaron las cosas, no *dónde*. Las trayectorias se reconstruyen pidiéndole las
rutas al mismo `BestPath` que usó el motor para decidir, y se resuelven a coordenadas acá. El
frontend entonces no sabe nada del dominio: interpola sobre polilíneas.

## Hallazgos

Seis rondas de iteración con Playwright, mirando capturas. Lo que apareció:

1. **El lienzo se comía los controles.** Se medía contra `.stage`, que incluye la fila de la línea de
   tiempo, así que el canvas crecía y empujaba los botones fuera de pantalla. Se agregó un
   `.viewport` y el canvas se mide solo contra él.
2. **La niebla se tragaba la mina.** El plano cercano estaba fijo en 4000 m y la mina mide 4000 m. Se
   deriva de la distancia de cámara.
3. **`kamada_kawai_layout` necesita scipy**, que no era dependencia. De ahí el MDS con numpy.
4. **Un proceso viejo de uvicorn seguía tomando el puerto** y servía código anterior: `data.values`
   llegaba `undefined`. En Windows `pkill` no sirve; se pasó a un puerto nuevo.
5. **Las etiquetas se pisaban en el fondo del pit.** Estaban escalonadas en metros, pero los sprites
   tienen `sizeAttenuation: false` —tamaño de pantalla constante—, así que a la distancia del pit la
   separación colapsaba a cero. Ahora el escalonado va en espacio de pantalla, vía `sprite.center`,
   que se mide en alturas de etiqueta.
6. **La pala caída no se veía caer**, que es justamente el tema de la demo 2. El replay no exponía
   `shovel_down` / `shovel_up`, aunque el motor ya los emitía. Ahora emite `shovel_states` y la pala
   se pinta roja, el brazo parpadea y el tablero marca `parada`.
7. **El parpadeo de "fuera de servicio" era un estrobo.** Estaba atado al tiempo simulado: a 600× un
   parpadeo de 3 segundos simulados son 100 Hz, que se lee como una falla de render. Ahora va contra
   el reloj de pared.

## Verificación

No hay forma de "testear" que una escena 3D se ve bien, pero sí de acotar el problema a eso. Los 21
tests de `apps/dispatch-web/tests/` cubren lo que sí es verificable:

- la geometría coloca todos los nodos, el pit queda bajo los botaderos y la elevación coincide con la
  pendiente de cada segmento;
- **ningún camión se teletransporta**: cada acarreo arranca donde terminó el anterior, y todo vértice
  de toda trayectoria es un nodo real de la red;
- las identidades contables del payload cierran —por destino y por ruta suman el total— y **lo que el
  tablero acumula desde los eventos coincide con lo que reporta el motor**, que es lo que evita un
  tablero que muestre números inventados;
- cada botón de demo apunta a un escenario y una política que existen (una tarjeta muerta es el bug
  que un comité sí o sí encuentra);
- el titular de la demo, aseverado y no solo dibujado: la heurística miope mueve **al menos tanto
  tonelaje** y produce **menos valor**.

Barrido final de las cinco demos en Chromium headless, corridas hasta el final de la línea de tiempo,
sin errores de consola:

| Demo | Escenario | t volteadas | Valor | Ley al chancador |
|---|---|---|---|---|
| Un turno normal | toy, 2 h | 4.400 | 13.200 | 0.767 en spec |
| Se cae una pala | toy-failure, 2 h | 4.620 | 9.900 | 0.767 en spec |
| Dos destinos | toy-stockpile, 4 h | 10.340 | 26.972 | 0.767 en spec |
| El mundo real | toy-variable, 4 h | 9.900 | 29.700 | 0.759 en spec |
| **Sin plan** | toy, 2 h, `earliest` | **5.060** | **11.220** | **0.820 fuera** |

La primera y la última fila son la demo, y desde la [etapa 13](13-seguimiento-del-plan.md) el
contraste es el completo: la misma mina, despachando al más cercano, **mueve más roca (5.060 contra
4.400), gana 15 % menos valor y entrega el mineral fuera de ley**. Los tres hechos en una pantalla.

## Decisiones y límites

- **El equipo está exagerado de escala.** Un camión a escala real sobre un pit de cuatro kilómetros
  son dos píxeles. Los caminos y las elevaciones son fieles; solo las máquinas están agrandadas, como
  en cualquier plano de mina.
- **Se dibujan líneas de plomada de cada nodo al piso de la grilla.** Sin eso el pit se lee plano:
  150 m de profundidad no son nada contra 4 km de ancho.
- **El camión que falla se detiene en el borde del ciclo, no a mitad de viaje.** Es la misma decisión
  de la etapa 06, y viene del dominio: no se representa la posición entre dos nodos.
- **No hay persistencia ni multiusuario.** Cada `POST /api/run` corre una simulación y devuelve el
  guion; no hay estado de servidor. Para una demo es lo correcto.

## Cómo correrla

```bash
uv run dispatch-demo          # abre el navegador solo
```
