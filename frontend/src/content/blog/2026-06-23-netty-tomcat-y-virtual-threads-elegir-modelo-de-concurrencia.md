---
title: "Netty, Tomcat y virtual threads: elegir modelo de concurrencia"
description: "Los virtual threads de Java permiten escribir código síncrono con la escalabilidad de un event-loop, eliminando el coste de los hilos OS en cargas I/O-bound. Netty sigue siendo superior cuando se necesitan millones de conexiones de larga duración con latencia mínima o cuando el ecosistema ya está construido sobre él. La decisión es un trade-off entre simplicidad de desarrollo y control fino del rendimiento."
date: 2026-06-23
tags: ["concurrency", "java"]
issue: 28
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
quiz:
  - question: "¿Cuál es la principal limitación del modelo thread-per-request tradicional en Tomcat para alta concurrencia?"
    options:
      - "Obliga a usar APIs no bloqueantes."
      - "El alto consumo de memoria de pila y el coste de context switching del kernel."
      - "No puede manejar más de 1000 conexiones simultáneas."
      - "Requiere un event-loop por cada núcleo."
    correct: 1
    explanation: "Cada hilo OS reserva ~1 MB de stack y el kernel debe planificarlos, lo que genera un overhead insostenible con decenas de miles de hilos. A es falsa porque el modelo thread-per-request usa APIs bloqueantes. C es un número arbitrario. D describe el modelo de Netty, no el de Tomcat."
  - question: "¿Qué situación puede degradar el rendimiento de los virtual threads?"
    options:
      - "Usarlos con Spring Boot."
      - "Ejecutar código que no realiza ninguna operación de I/O."
      - "Tareas CPU-bound que provocan pinning del carrier thread."
      - "Utilizarlos en un contenedor con más de 4 núcleos."
    correct: 2
    explanation: "Si un virtual thread ejecuta cálculos intensivos sin puntos de bloqueo, acapara el carrier thread e impide que otros virtual threads progresen. A y D no son problemas reales. B es incorrecta porque los virtual threads funcionan bien con código que no hace I/O, siempre que no cause pinning prolongado."
  - question: "¿En cuál de estos escenarios Netty sigue siendo claramente preferible a los virtual threads?"
    options:
      - "Un microservicio REST que consulta una base de datos."
      - "Un servidor WebSocket con 500 000 conexiones activas y requisitos de latencia inferiores a 1 ms."
      - "Una aplicación batch que procesa archivos CSV."
      - "Un monolito Java EE migrado a Jakarta EE 10."
    correct: 1
    explanation: "Netty ofrece control directo sobre la asignación de hilos y evita el overhead del scheduler de virtual threads, crucial para latencias extremas y cientos de miles de conexiones. A se beneficia de virtual threads por su simplicidad. C es CPU-bound y no necesita ni Netty ni virtual threads. D puede usar virtual threads sin problemas."
reviewer: "minimax/minimax-m3"
---

## El coste oculto del thread-per-request

El modelo clásico de Tomcat asigna un hilo del sistema operativo a cada petición HTTP. El hilo se bloquea esperando I/O —lectura de base de datos, llamada a otro servicio— y el kernel lo desaloja hasta que los datos llegan. Para el desarrollador, el código es lineal y sencillo: una traza, una petición, un hilo.

El problema aparece con la concurrencia. Cada hilo OS consume alrededor de 1 MB de stack y fuerza cambios de contexto que el kernel gestiona con un coste creciente. Con 10 000 peticiones simultáneas, el servidor puede dedicar más tiempo a *scheduling* que a procesar. La solución tradicional ha sido limitar el pool de hilos y rechazar peticiones cuando se satura, o adoptar un modelo asíncrono.

## La alternativa asíncrona: el event-loop de Netty

Netty implementa un modelo *reactor* con un número fijo de hilos —típicamente dos por núcleo— que nunca se bloquean en I/O. Cada hilo ejecuta un *event-loop* que sondea múltiples canales con un `Selector` (epoll/kqueue). Cuando un canal está listo, el hilo procesa el evento y pasa al siguiente. No hay un hilo por conexión; miles de canales comparten unos pocos hilos.

Esto elimina el problema de la pila y el *context switching* masivo, pero traslada la complejidad al código. El programador debe encadenar callbacks, promesas o usar *reactive streams* (Project Reactor, RxJava). La lógica de negocio se fragmenta, la depuración se complica y el *stack trace* pierde sentido. Netty ofrece máximo rendimiento en conexiones de larga duración (WebSockets, proxies, servidores de juegos) a costa de una curva de aprendizaje pronunciada.

## Virtual threads: ¿lo mejor de ambos mundos?

Los *virtual threads* (JEP 425) son hilos ligeros gestionados por la JVM, no por el sistema operativo. Cada uno tiene su propia pila, pero esta se almacena en el heap y puede crecer y reducirse dinámicamente, ocupando típicamente unos pocos cientos de bytes. El scheduler de la JVM mapea decenas de miles de virtual threads sobre un pequeño pool de *carrier threads* (hilos OS reales).

Cuando un virtual thread ejecuta una operación bloqueante, la JVM detecta la llamada y libera el carrier thread para que ejecute otro virtual thread. El virtual thread original queda suspendido en el heap hasta que la operación se completa. Esto permite escribir código síncrono, con bloqueos naturales, y escalar a millones de conexiones sin el coste de millones de hilos OS.

Tomcat 10 y Spring Boot 3.2 ya soportan virtual threads: basta con configurar `spring.threads.virtual.enabled=true` y el contenedor ejecuta cada petición en un virtual thread. El código sigue igual, pero la escalabilidad en cargas I/O-bound se dispara.

Sin embargo, los virtual threads no son una bala de plata. Si una tarea realiza cálculos intensivos sin puntos de bloqueo, acapara el carrier thread y produce *pinning*: otros virtual threads no pueden progresar. Además, ciertas operaciones nativas o bloques `synchronized` también provocan pinning. En esos casos, el rendimiento puede degradarse al de un pool de hilos OS pequeño. La solución es aislar tareas CPU-bound en un pool de hilos OS tradicional y usar virtual threads solo para I/O.

## Cuándo Netty sigue siendo la respuesta

Netty mantiene ventajas en escenarios extremos:

- **Millones de conexiones de larga duración** (proxies, brokers, game servers). Aunque los virtual threads reducen la memoria por conexión, el scheduler de la JVM y la infraestructura de continuaciones añaden overhead. Netty, con su modelo de event-loop y asignación manual de buffers, ofrece un control más fino y menor latencia en la cola de scheduling.
- **Ecosistemas que ya usan Netty**: gRPC-Java, Reactor Netty, etc. Migrar a virtual threads puede no aportar suficiente beneficio frente al coste de reescribir.
- **Operaciones no bloqueantes puras**: si toda la lógica es asíncrona y no hay bloqueos, el modelo de event-loop evita el cambio de contexto incluso entre virtual threads, que aunque ligero, existe.

## Un caso práctico: 10 000 conexiones concurrentes

Imaginemos un servidor HTTP que recibe peticiones y consulta una base de datos con 50 ms de latencia. Con Tomcat clásico y un pool de 200 hilos, el throughput máximo es de 4000 peticiones/segundo (200 / 0.05). Para 10 000 conexiones concurrentes, el pool se satura y las peticiones se encolan.

Con Tomcat + virtual threads, cada petición tiene su propio hilo virtual. Los 10 000 hilos virtuales se ejecutan sobre unos pocos carrier threads. Cuando un virtual thread espera la base de datos, libera el carrier, que atiende a otro. El throughput se acerca al límite de la base de datos, no al del pool de hilos. El código sigue siendo síncrono.

Con Netty, el mismo escenario se maneja con un event-loop de 4 hilos. Las conexiones se registran en un `Selector` y las operaciones de I/O se despachan sin bloqueo. El throughput es similar al de virtual threads, pero el código es asíncrono: callbacks o `Mono`/`Flux` de Reactor. La ventaja de Netty aparece cuando las conexiones son cientos de miles y la latencia debe ser mínima, porque el scheduler de virtual threads añade una capa de scheduling que Netty evita.

## Conclusión

Los virtual threads acercan el modelo simple de thread-per-request a la escalabilidad de los event-loops, y son la opción correcta para la mayoría de aplicaciones empresariales I/O-bound. Netty sigue siendo superior cuando el control fino sobre los hilos y la memoria es crítico, o cuando el ecosistema ya está construido sobre él. La decisión no es técnica binaria, sino económica: simplicidad de desarrollo frente a rendimiento extremo.

---

### Quiz de comprensión

**1. ¿Cuál es la principal limitación del modelo thread-per-request tradicional en Tomcat para alta concurrencia?**  
A) Obliga a usar APIs no bloqueantes.  
B) El alto consumo de memoria de pila y el coste de *context switching* del kernel.  
C) No puede manejar más de 1000 conexiones simultáneas.  
D) Requiere un event-loop por cada núcleo.  
**Respuesta correcta: B (índice 1)**  
*Explicación*: Cada hilo OS reserva ~1 MB de stack y el kernel debe planificarlos, lo que genera un overhead insostenible con decenas de miles de hilos. A es falsa porque el modelo thread-per-request usa APIs bloqueantes. C es un número arbitrario. D describe el modelo de Netty, no el de Tomcat.

**2. ¿Qué situación puede degradar el rendimiento de los virtual threads?**  
A) Usarlos con Spring Boot.  
B) Ejecutar código que no realiza ninguna operación de I/O.  
C) Tareas CPU-bound que provocan *pinning* del carrier thread.  
D) Utilizarlos en un contenedor con más de 4 núcleos.  
**Respuesta correcta: C (índice 2)**  
*Explicación*: Si un virtual thread ejecuta cálculos intensivos sin puntos de bloqueo, acapara el carrier thread e impide que otros virtual threads progresen. A y D no son problemas reales. B es incorrecta porque los virtual threads funcionan bien con código que no hace I/O, siempre que no cause pinning prolongado.

**3. ¿En cuál de estos escenarios Netty sigue siendo claramente preferible a los virtual threads?**  
A) Un microservicio REST que consulta una base de datos.  
B) Un servidor WebSocket con 500 000 conexiones activas y requisitos de latencia inferiores a 1 ms.  
C) Una aplicación batch que procesa archivos CSV.  
D) Un monolito Java EE migrado a Jakarta EE 10.  
**Respuesta correcta: B (índice 1)**  
*Explicación*: Netty ofrece control directo sobre la asignación de hilos y evita el overhead del scheduler de virtual threads, crucial para latencias extremas y cientos de miles de conexiones. A se beneficia de virtual threads por su simplicidad. C es CPU-bound y no necesita ni Netty ni virtual threads. D puede usar virtual threads sin problemas.
