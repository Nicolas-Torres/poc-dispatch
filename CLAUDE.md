# CLAUDE.md

Este archivo da indicaciones a Claude Code (claude.ai/code) para trabajar con el código de este repositorio.

## Estado del proyecto

Este repositorio actualmente contiene **solo documentos de planificación/referencia bajo `docs/`** — no hay
código fuente, no hay manifiesto de paquete, y todavía no se ha inicializado un repositorio git. No existen
comandos de build, lint o test porque aún no se ha creado ningún andamiaje (scaffolding). Antes de asumir
que existe alguna herramienta (versión de Python, gestor de paquetes, framework de tests), verificar si se
ha añadido desde entonces.

`docs/plan.md` indica que el stack previsto es **Python + uv**, con una pregunta abierta sobre si un layout
de uv workspace se ajusta a la necesidad del proyecto — esto aún no está decidido ni implementado.

## Qué es este proyecto

Objetivo (desde `docs/requisitos/objetivo.md`): desarrollar un **sistema de despacho de camiones para
minería a cielo abierto**, basado en DISPATCH de Modular Mining, usable y customizable en la medida que un
sistema de dispatch real lo permita. Funcionará como el motor de asignación dentro de un **gemelo digital de
una mina** (simulando una mina a cielo abierto cualquiera, no una mina real específica).

## Modelo de dominio: cómo funciona la asignación estilo DISPATCH

Los documentos de referencia (`docs/contexto/motor_asignacion_dispatch_mineria.md` y
`docs/contexto/dispatch_motor_asignacion.md`) sintetizan literatura académica y patentes públicas sobre el
algoritmo real de DISPATCH. Cualquier implementación del motor de asignación debería seguir esta
descomposición en tres etapas:

1. **Best Path** — modela la red de caminos de la mina como un grafo dirigido (zonas de carga, zonas de
   descarga, callpoints e intersecciones como nodos; segmentos de vía con sentido, pendiente y velocidad
   como aristas con peso). Calcula las rutas más cortas/rápidas entre todos los pares punto de
   carga↔descarga. Se recalcula solo cuando cambia la topología de la red (se cierra una vía, se abre una
   rampa). Los tiempos de viaje resultantes alimentan las dos etapas siguientes.

2. **Optimización macro (Programación Lineal)** — resuelve las tasas de flujo de material ideales `x_r`
   (toneladas/hora o viajes/hora) por ruta `r` (una ruta = zona de carga + zona de descarga + camino +
   registro de ley del material + tipo de vehículo), maximizando la producción / minimizando el costo de
   acarreo, sujeto a: capacidad de excavación de la pala, capacidad de recepción del destino
   (chancadora/botadero), conservación de flujo, restricciones de blending/ley, prioridades entre palas y
   disponibilidad de flota. La literatura la describe como **dos LP débilmente acoplados**, que se
   re-resuelven a intervalos o cuando cambian las condiciones (pala en falla, cambio de material, cambio de
   flota) — no en cada solicitud de camión.

3. **Asignación en tiempo real (Programación Dinámica / colas)** — se dispara por camión, típicamente
   cuando un camión termina de descargar y necesita un nuevo destino. Compara el acarreo real asignado
   contra las tasas objetivo del LP para encontrar la pala/ruta más "necesitada" (asignado < requerido), y
   luego elige el par camión/pala que minimiza el tiempo ocioso total (palas ociosas esperando vs. camiones
   en cola). Es un **lookahead sobre varios camiones, pero solo se confirma la asignación del camión
   actual** — el problema se vuelve a resolver en la siguiente solicitud. La literatura lo clasifica como
   "**m camiones para 1 pala**" (no la heurística más simple "1 camión para n palas", como asignar a la
   pala con menor tiempo de espera o a la que lleva más tiempo sin camión).

   El procedimiento descrito en la patente para la asignación de camiones vacíos (US 11,187,547) es una
   referencia útil, casi pseudocódigo:
   - Snapshot: `Ta(s)` = camiones en la pala `s`, yendo hacia ella o proyectados a ella; `T'` = camiones
     que requieren o requerirán pronto una asignación; `Tc(s)` = camiones despachables a la pala `s`
     (subconjunto de `T'`).
   - Calcula valores de acarreo por pala: *requerido* (según el plan del LP) vs. *asignado* (suma de
     payloads de los camiones en, o yendo hacia, cada zona) — una pala/ruta está "necesitada" cuando
     asignado < requerido.
   - Ordena palas/rutas por necesidad decreciente; ordena los camiones candidatos por tiempo esperado de
     asignación.
   - Evalúa los pares candidatos por penalidad (tiempo ocioso total de palas + camiones); asigna el par de
     menor penalidad; actualiza los valores de acarreo y repite hasta decidir el destino del camión que
     solicitó la asignación.
   - Las restricciones operativas (solo acarreos cortos, reducción de velocidad, reducción de carga)
     modifican la pertenencia a las listas, las ETA proyectadas y los valores de acarreo asignado durante
     el procedimiento.

   Ciclo de vida del equipo: cada unidad sigue una máquina de estados de ciclo (viaje vacío → cola →
   cuadrado → carga → viaje cargado → cola en descarga → descarga), además de códigos de estado
   (operativo, demora, standby, malogrado). El despachador puede intervenir manualmente sobre el algoritmo
   (fijar un camión a una pala, excluir equipos, cambiar prioridades).

`docs/contexto/dispatch_motor_asignacion.md` §2.7 da un orden de lectura sugerido para la literatura fuente
(reporte MOL 2017 → patente US 11,187,547 → Alarie y Gamache 2002 → papers de White/Olson) y §4 lista
posibles siguientes pasos (prototipar el LP con OR-Tools, simulación de eventos discretos con
SimPy/OpenMines, replicar el procedimiento de asignación de la patente) — contexto útil si se pide acotar
el trabajo de implementación, pero aún no decidido.

## Idioma

- **Conversación con el usuario**: español.
- **Commits y código** (identificadores, comentarios, mensajes de commit, títulos/descripciones de PR):
  inglés.

## Flujo de trabajo (desde `docs/plan.md`)

- **GitHub Flow**: ramas como `feature/*`, PR hacia `main` (sin commits directos a `main`).
- **Commits intermedios**: Conventional Commits, **solo el subject line, sin cuerpo** —
  ej.: `feat(db): add threat_indicators governance table`.
- **Descripción del PR**: máximo 6 líneas.
