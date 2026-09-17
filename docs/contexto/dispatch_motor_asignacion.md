# Motor de asignación DISPATCH (Modular Mining)

Recopilación de la conversación sobre el funcionamiento del motor de asignación de DISPATCH y la documentación técnica pública disponible.

*Fecha: 17 de septiembre de 2026*

---

## 1. Qué es DISPATCH y cómo funciona su motor de asignación

**DISPATCH** es el sistema de gestión de flotas (FMS) de **Modular Mining**, empresa de Komatsu, y es probablemente el más usado en minería a tajo abierto. Su motor de asignación decide en tiempo real a qué pala (y luego a qué botadero o chancadora) va cada camión.

La implementación exacta es propietaria. Lo que se conoce públicamente viene sobre todo de:

- los papers de sus creadores: White, Arnold y Clevenger (1982), sobre la mina Tyrone; White y Olson (1986); Olson, Vohnout y White (1993);
- revisiones académicas como la de Alarie y Gamache (2002);
- patentes de Modular Mining (ver sección 2.1).

Según esa literatura, el motor se descompone en tres etapas.

### 1.1 Best Path (rutas óptimas)

- La red de caminos de la mina se modela como un grafo: nodos (zonas de carga, zonas de descarga, callpoints, intersecciones) y aristas dirigidas (segmentos de vía con sentido de circulación, pendientes y velocidades).
- Se calculan las rutas de costo mínimo (más cortas o más rápidas) entre todos los puntos de carga y descarga.
- Solo se recalcula cuando cambia la red, por ejemplo si se cierra una vía o se abre una rampa.
- Los tiempos de viaje resultantes alimentan las etapas siguientes.

### 1.2 Programación lineal (el plan de producción)

Un LP calcula las **tasas de flujo ideales** en cada ruta pala→destino, en toneladas o viajes por hora. El objetivo suele ser maximizar la producción o minimizar los recursos de acarreo necesarios.

La literatura describe esta etapa como **dos LP débilmente acoplados**, resueltos en intervalos predefinidos.

Restricciones típicas:

- capacidad de excavación de cada pala;
- capacidad de los destinos (una chancadora no puede recibir más de X t/h);
- continuidad de flujo;
- **blending**: leyes o calidades del mineral dentro de rangos en la chancadora;
- prioridades entre palas;
- tamaño de la flota disponible (la suma de flujo × tiempo de ciclo está limitada).

El LP se re-resuelve cuando cambian las condiciones: una pala entra en demora o falla, cambia el material, o se agrega o retira equipo.

Formulación descrita en la patente US 11,187,547:

```
(P): max  f(x) = cᵀx
     s.a. Hx = b
          x ≥ 0
```

- `x_r` es la tasa de flujo de material en la ruta `r`.
- Una "ruta" agrupa: zona de carga, zona de descarga, el camino entre ambas, un registro de ley del material y un tipo de vehículo.
- `c_r` es el valor de productividad por unidad de flujo en la ruta `r`.
- `Hx = b` recoge tasas de producción de palas, capacidades de procesamiento en destinos y disponibilidad de camiones por tipo de flota.

### 1.3 Asignación en tiempo real (programación dinámica)

Cuando un camión necesita destino, típicamente al terminar de descargar, el sistema compara la realidad contra el plan del LP:

- De cada tasa de flujo se deduce un intervalo ideal entre camiones por ruta.
- Las rutas o palas que van más atrasadas respecto al plan son las más "necesitadas".
- La decisión no es miope: se consideran los próximos camiones que van a requerir asignación y se minimiza el tiempo perdido total, tanto de palas ociosas esperando camión como de camiones haciendo cola en la pala.
- Aunque se resuelve para varios camiones, solo se confirma la asignación del camión actual y se vuelve a resolver en la siguiente solicitud.
- En términos simples: enviar el camión más conveniente a la pala más necesitada.

> **Corrección respecto a la primera respuesta del chat:** inicialmente se clasificó DISPATCH como estrategia "m camiones para n palas". La literatura (Alarie y Gamache, 2002; Moradi Afrapoli et al., 2019) lo clasifica como **"m camiones para 1 pala"**: se consideran los m camiones que necesitarán asignación pronto para una sola pala necesitada. Se contrapone a heurísticas más simples de "1 camión para n palas", como asignar a la pala con menor tiempo de espera o a la que lleva más tiempo sin recibir camión.

#### Procedimiento de asignación de camiones vacíos (según patente US 11,187,547)

1. **Snapshot del sistema.** Se construyen las listas:
   - `Ta(s)`: camiones que están en la pala `s`, van hacia ella o se proyecta despacharlos a ella (llegadas).
   - `T'`: camiones que requieren o requerirán pronto una asignación a pala (candidatos generales).
   - `Tc(s)`: camiones que pueden despacharse a la pala `s` (subconjunto de `T'`).
2. **Valores de acarreo:**
   - *Requerido*: recursos de camión necesarios para cumplir el plan de producción.
   - *Asignado*: suma de payloads de los camiones que están en, o van hacia, cada zona de carga.
   - Una ruta o pala es **necesitada** si lo asignado es menor que lo requerido.
3. **Ordenamiento:** palas `S` y rutas vacías `R'` en orden decreciente de necesidad; `T'` según el tiempo esperado de asignación de cada camión.
4. **Evaluación con penalidades** (penalidad = tiempo ocioso total de palas y camiones):
   - para una pala, se busca el camión candidato cuya asignación tenga menor penalidad;
   - para un camión, se busca la pala de menor penalidad entre las que lo incluyen como candidato.
5. Al "asignar" un camión se actualizan los valores de acarreo y las listas. El proceso termina cuando se identifica el destino del camión que pidió asignación.

La misma patente muestra cómo se integran restricciones operativas en el procedimiento: acciones como "solo acarreos cortos", "reducción de velocidad" o "reducción de carga" alteran la pertenencia a las listas candidatas, los tiempos de llegada previstos y los valores de acarreo asignado. También menciona parámetros de configuración como `SH_PARAM` (umbral porcentual para definir acarreos cortos).

### 1.4 Lo operativo alrededor del motor

- Los equipos tienen GPS (las palas suelen tener alta precisión) y terminales a bordo conectados por red inalámbrica.
- Cada equipo sigue una máquina de estados del ciclo: viaje vacío → cola → cuadrado → carga → viaje cargado → cola en descarga → descarga.
- Existen códigos de estado: operativo, demora, standby, malogrado.
- El despachador puede intervenir manualmente, por ejemplo fijando camiones a una pala (asignación "locked"), excluyendo equipos o cambiando prioridades.
- Todo eso genera una base histórica de ciclos, tiempos y eventos que es la fuente usual de KPIs de acarreo.

**Competidores con enfoques comparables:** Cat MineStar Fleet, Hexagon Mining (antes Jigsaw) y Wenco.

---

## 2. Documentación técnica disponible

La lógica exacta del código no está publicada. La literatura académica reconoce que, por el carácter comercial del sistema, probablemente no se publicaron todos los detalles del algoritmo. Aun así, hay bastante material público, ordenado aquí de más a menos cercano a la implementación real.

### 2.1 Patentes de Modular Mining (lo más cercano a documentación interna)

- **US 11,187,547**, "Tire conditioning optimization for a collection of mining vehicles" (2021; inventores Lucas Van Latum y Maria Brenda R. Rayco). Continuación de una solicitud de 2016.
  - Aunque el tema son los neumáticos, describe el pipeline completo del despacho: módulo Best Path, módulo de plan de producción (LP) y procedimiento de asignación de tareas.
  - Es texto completo y gratuito, y prácticamente se puede traducir a pseudocódigo (ver sección 1.3).
- **US 12,130,148**: continuación de la anterior (2024).
- El resto de patentes de Modular (guiado de maniobras, zonas de colisión proyectadas, gestión de energía, selección de destino para navegación) son menos relevantes para el motor de asignación.

### 2.2 Papers de los creadores

- **White, J.W., Arnold, M.J. y Clevenger, J.G. (1982).** Despacho automatizado en la mina Tyrone. *Engineering & Mining Journal*. Es el origen histórico.
- **White, J.W. y Olson, J.P. (1986).** "Computer-based dispatching in mines with concurrent operating objectives". *Mining Engineering*.
  - Enfocado en el algoritmo para cumplir objetivos simultáneos: minimizar rehandle, cumplir varias restricciones de blending y asegurar una tasa objetivo de alimentación a planta.
  - Existe también una versión en APCOM 1987.
- **Olson, J.P., Vohnout, S.I. y White, J.W. (1993).** "On improving truck/shovel productivity in open pit mines". *CIM Bulletin*.
  - Revisa la literatura, presenta algoritmos óptimos y eficientes para despacho en tiempo real y reporta resultados en trece operaciones.
  - Disponible en OneMine (de pago).
- **White, J.W. (1989).** "Automated haulage control worldwide". ICCAMI '89. De carácter más general e histórico.

### 2.3 Revisiones que reconstruyen el algoritmo

- **Alarie, S. y Gamache, M. (2002).** "Overview of solution strategies used in truck dispatching systems for open pit mines". *IJSMRE*, 16(1), 59–76. Es la referencia clásica de clasificación de estrategias.
- **Munirathinam, M. y Yingling, J.C. (1994).** "A review of computer-based truck dispatching strategies for surface mining operations". *IJSMRE*, 8, 1–15. Clasifica las estrategias de despacho y analiza en detalle sus formulaciones matemáticas.
- **Moradi Afrapoli, A. y Askari-Nasab, H. (2019).** "Mining fleet management systems: a review of models and algorithms". *IJMRE*, 33(1), 42–60. Divide los FMS en tres problemas encadenados: camino más corto, optimización de producción y despacho en tiempo real.

### 2.4 Reimplementaciones académicas (las más útiles para replicar)

El **Mining Optimization Laboratory (MOL) de la Universidad de Alberta** reconstruyó el "backbone" de DISPATCH como benchmark:

- **Reporte MOL 2017**, "An Investigation into Dispatch Optimizers using Truck-Shovel Simulation and a New Multi Objective Truck Dispatching Technique" (PDF gratuito).
  - Describe los dos LP débilmente acoplados y la etapa de programación dinámica que asigna el camión más cercano a la pala más necesitada.
  - Implementa un heurístico basado en simulación que sigue ese backbone.
- **Moradi Afrapoli, A., Tabesh, M. y Askari-Nasab, H. (2019).** "A multiple objective transportation problem approach to dynamic truck dispatching in surface mines". *European Journal of Operational Research*, 276(1), 331–342.
  - Construye un modelo benchmark basado en DISPATCH.
  - Lo compara, en una simulación de eventos discretos de una mina de hierro, contra un modelo de transporte multiobjetivo con goal programming.
- **Mohtasham, M. et al. (2022).** "Multi-stage optimization framework for the real-time truck decision problem in open-pit mines: a case study on Sungun copper mine". *IJMRE*, 36(7). Implementa el algoritmo de DISPATCH como referencia.

Trabajos recientes que usan DISPATCH como punto de comparación:

- Despachador con Deep Reinforcement Learning (*Computers & Operations Research*, 2024).
- Algoritmo genético para despacho (GCAI 2017).
- **OpenMines**: entorno de simulación ligero para despacho de camiones (arXiv 2404.00622), útil para prototipos.

### 2.5 Contraste: lado Caterpillar MineStar

- **US 6,741,921**, "Multi-stage truck assignment system and method" (titular: Caterpillar Inc.; inventores: Paul J. Cohen, Stéphane Alarie y Michel Gamache).
  - No es DISPATCH, pero es útil como comparación.
  - Llama la atención que los autores de la revisión de 2002 sean inventores de esta patente.

### 2.6 Lo que no es público

- Manuales de usuario y de configuración.
- Documentación de parámetros del optimizador.
- Esquema de base de datos.

Hasta donde se sabe, Modular los entrega a las minas clientes bajo licencia. Si la empresa usa DISPATCH, la vía de acceso sería el soporte de Modular.

### 2.7 Orden de lectura sugerido

1. Reporte MOL 2017 (gratuito).
2. Patente US 11,187,547 (gratuita y la más concreta).
3. Alarie y Gamache (2002).
4. White y Olson (1986) y Olson et al. (1993), si se consigue acceso.

---

## 3. Enlaces

| Recurso | Enlace |
|---|---|
| Patente US 11,187,547 (Modular Mining) | https://patents.justia.com/patent/11187547 |
| Listado de patentes de Modular Mining | https://patents.justia.com/assignee/modular-mining-systems-inc |
| Patente US 6,741,921 (Caterpillar) | https://patents.google.com/patent/US6741921 |
| White y Olson (1986), OSTI | https://www.osti.gov/biblio/7015726 |
| White y Olson (1986), Semantic Scholar | https://www.semanticscholar.org/paper/Computer-based-dispatching-in-mines-with-concurrent-White-Olson/6a5a99f155785aaec9a264c605913328973e611e |
| Olson, Vohnout y White (1993), OneMine | https://www.onemine.org/documents/on-improving-truck-shovel-productivity-in-open-pit-mines |
| White (1989), OSTI | https://www.osti.gov/etdeweb/biblio/7015133 |
| Alarie y Gamache (2002) | https://doi.org/10.1076/ijsm.16.1.59.3408 |
| Moradi Afrapoli y Askari-Nasab (2019), revisión | https://doi.org/10.1080/17480930.2017.1336607 |
| Reporte MOL 2017 (PDF) | https://sites.ualberta.ca/MOL/DataFiles/2017_Papers/201_%20An%20Investigation%20into%20Dispatch%20Optimizers%20using%20Truck-Shovel%20Simulation%20and%20a%20New%20Multi%20Objective%20Truck%20Dispatching%20Technique.pdf |
| Reporte MOL 2017, FMS multiobjetivo (PDF) | https://sites.ualberta.ca/mol/DataFiles/2017_Papers/102_A%20Multi%20Objective%20Multi%20Stage%20Mining%20Fleet%20Management%20System%20Linking%20Dynamic%20Operation%20to%20Short-Term%20Plan.pdf |
| Moradi Afrapoli et al. (2019), EJOR | https://doi.org/10.1016/j.ejor.2019.01.008 |
| Mohtasham et al. (2022), Sungun | https://doi.org/10.1080/17480930.2022.2067709 |
| DRL para despacho (C&OR, 2024) | https://www.sciencedirect.com/science/article/abs/pii/S0305054824002879 |
| Algoritmo genético (GCAI 2017) | https://easychair.org/publications/paper/3PFP/open |
| DRL para despacho (workshop AAAI-25) | https://womapf.github.io/aaai-25/pdf/Submission_23.pdf |
| OpenMines (arXiv) | https://arxiv.org/abs/2404.00622 |

---

## 4. Posibles siguientes pasos

- Convertir el procedimiento de asignación de la patente US 11,187,547 en pseudocódigo o en un prototipo en Python.
- Prototipar la etapa LP con OR-Tools y la asignación en tiempo real sobre una simulación de eventos discretos (SimPy u OpenMines).
- Trabajar con los datos históricos que genera el sistema (ciclos, estados, eventos) para KPIs de acarreo.
