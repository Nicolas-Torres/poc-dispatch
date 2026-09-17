# Motor de Asignación en Minería: Modular Mining DISPATCH

## Overview y Funcionamiento General

**Modular Mining DISPATCH** (o simplemente **Dispatch**) es el sistema de gestión de flotas (*Fleet Management System* - FMS) más utilizado en la minería a cielo abierto a nivel mundial para optimizar en tiempo real el transporte y la asignación de camiones hacia las palas o puntos de descarga.

---

### ¿Cómo funciona el motor de asignación?

El núcleo de Dispatch utiliza algoritmos de optimización matemática en dos niveles principales para maximizar la producción y minimizar tiempos muertos:

1. **Nivel Macro (Programación Lineal):**
   - Calcula de forma global los flujos óptimos de material (toneladas/hora) entre los puntos de carga (palas), de descarga (chancadoras, botaderos, *stockpiles*) y mezclado (*blending*).
   - Considera restricciones como la capacidad de las plantas, mezclas de ley (*grade control*), prioridades de alimentación y capacidades teóricas del circuito.

2. **Nivel Micro (Programación Dinámica y Colas):**
   - En tiempo real, cada vez que un camión se libera (por ejemplo, al terminar de descargar), el motor evalúa la posición actual, tiempos de tránsito, tiempos de espera proyectados en cola y estado de las palas.
   - Asigna el camión específico a la pala óptima para mantener el flujo constante y reducir las colas en las palas y chancadoras.

---

### Variables clave que procesa en tiempo real

- **Telemetry & GPS:** Ubicación, velocidad, sentido de marcha y tiempo estimado de arribo (ETA).
- **Estado de los equipos:** Disponibilidad mecánica, mantenimiento planificado, tiempos de ciclo (carga, acarreo, descarga, retorno).
- **Restricciones operativas:** Prioridad de minado por fase, límites de mezcla en chancado, cuellos de botella en vías de tránsito y restricciones de seguridad.
- **Eventos no planificados:** Demoras por voladura, cambios de turno, fallas en pala o bloqueos de vías.

---

### Beneficios principales

- **Incremento de productividad:** Entre un 5% y 15% de incremento en el ton/hora movido frente a una asignación fija o manual.
- **Reducción del tiempo en cola:** Maximiza el uso eficiente del camión y disminuye el consumo de combustible innecesario.
- **Control de leyes:** Asegura la calidad de mezcla requerida por la planta concentradora ajustando el origen del material enviado a chancado.

---

## Documentación Técnica y Lógica Interna de Programación

Existe literatura técnica y académica detallada que documenta la lógica interna y las matemáticas del motor de asignación de **DISPATCH** (desarrollado originalmente por **Modular Mining Systems**, hoy filial de Komatsu). 

Aunque el código fuente exacto y los módulos propietarios de software son secreto comercial, el modelo matemático básico sobre el que fue construido Dispatch es público y ampliamente estudiado en investigación operativa (*Operations Research*).

---

### 1. Artículos académicos de los creadores (Papeles fundacionales)

El sistema original fue diseñado por **James W. White** y **Michael J. Arnold** a finales de los años 70 e inicios de los 80. Sus publicaciones son la fuente primaria más fiel sobre la arquitectura interna del algoritmo:

- **White, J. W., Arnold, M. J., & Clevenger, J. G. (1982).** *Automated open pit mine dispatching at Pima.* **AIME Transactions / Mining Engineering.**
  - *Contenido:* Documenta la implementación inicial del algoritmo en la mina Pima (Arizona). Detalla el enfoque de dos niveles: la formulación de **Programación Lineal (LP)** para la macro-asignación y la **Programación Dinámica (DP)** combinada con teoría de colas para la asignación en tiempo real a nivel micro.
- **White, J. W., & Olson, J. P. (1986).** *Computer-based dispatching in open pit mines.* **Operations Research in the Minerals Industry (APCOM).**
  - *Contenido:* Explica con ecuaciones el cálculo de tiempos de tránsito dinámicos (calculados mediante algoritmos de ruta más corta tipo Dijkstra sobre el grafo de la red de caminos) y cómo se resuelve la matriz de asignación.
- **White, J. W. (1993).** *Real-time dispatching concepts.* **Technical Paper, Modular Mining Systems.**
  - *Contenido:* Desglosa la transición del modelo estático a la asignación reactiva continua basada en la minimización de tiempos de espera en cola (*queueing delays*).

---

### 2. Formulación matemática interna del algoritmo

La documentación técnica describe el motor dividido operativamente en dos partes principales:

#### A. Módulo de Programación Lineal (LP) — *Macro Optimization*
Resuelve a intervalos regulares (o cuando cambia la configuración de la mina) el flujo objetivo $x_{ij}$ (toneladas o viajes por hora desde la pala $i$ hasta la descarga $j$):

- **Función Objetivo:** Maximizar el tonelaje total producido o minimizar los costos operativos/distancias de acarreo:
  $$\max \sum_{i} \sum_{j} x_{ij}$$
- **Sujeto a restricciones:**
  - Capacidad de excavación de cada pala $i$.
  - Capacidad de recepción de cada chancadora/botadero $j$.
  - Restricciones de mezcla (*blending*): Leyes de mineral ($\%Cu$, $\%Fe$, etc.) dentro de rangos requeridos en la planta concentradora.
  - Ley de conservación de flujo en la red de caminos (*route capacities*).

#### B. Módulo de Programación Dinámica y Teoría de Colas (DP) — *Micro Optimization*
Cada vez que un camión solicita una ruta (en el punto de descarga o durante el retorno), el motor evalúa una función de pérdida o tiempo de ciclo esperado:

- Aplica redes de colas cerradas (*Closed Queueing Networks* - modelo de Gordon-Newell o aproximaciones M/G/1) para predecir el tiempo que el camión esperará en la pala $i$.
- Minimiza el costo de oportunidad o la desviación respecto a las tasas $x_{ij}$ calculadas por la LP, seleccionando el destino que optimiza la eficiencia global en ese instante específico.

---

### 3. Tesis doctorales y libros de referencia

Para profundizar en las ecuaciones, pseudocódigos y variantes utilizadas por Dispatch y otros FMS modernos, las siguientes referencias son consideradas el estándar técnico:

- **Alarie, S., & Gamache, M. (2002).** *Overview of Solution Strategies for the Real-Time Truck Dispatching Problem en Open-Pit Mines.* **International Journal of Surface Mining, Reclamation and Environment.**
  - *Por qué leerlo:* Es el *paper* de revisión más citado sobre el tema. Analiza y compara la lógica interna de Dispatch frente a otros sistemas (como IntelliMine/Wenco, JMS, etc.), descomponiendo sus algoritmos de dos etapas y métodos de simulación en tiempo real.
- **Lizotte, Y., & Bonates, E. (1987).** *Truck dispatching strategies in open-pit mines.* **CIM Bulletin.**
  - *Por qué leerlo:* Desarrolla los modelos de teoría de grafos y teoría de colas exactos aplicados a la asignación de flotas heterogéneas (camiones de diferentes capacidades combinados con palas de distinto tamaño).
- **Subrutinas de Red de Caminos (Dijkstra / A* adaptados):**
  - La documentación técnica de la patente original de Modular Mining (US Patent 4,841,446) muestra el uso de modelos de grafos dirigidos con pesos dinámicos, donde el peso de cada arista no es solo la distancia física, sino una función de la pendiente, velocidad de restricción y tráfico acumulado en el tramo.

---

### Dónde encontrar este material

- **APCOM Conferences:** Las memorias de los congresos *Applications of Computers and Operations Research in the Mineral Industry* (APCOM) concentran la mayor cantidad de artículos técnicos de Modular Mining.
- **ResearchGate / IEEE Xplore / ScienceDirect:** Buscando por los términos `"Truck Dispatching Problem" "Open Pit" "Modular Mining" "James W. White"`.
- **Google Patents:** Buscar la patente `US4841446A` (*Mine dispatching system and method*), que detalla el flujo conceptual del hardware/software de telemetría y procesamiento de asignaciones en tiempo real.
