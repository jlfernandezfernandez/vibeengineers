---
title: "Java 21 a 25: Virtual Threads, Pattern Matching y Concurrencia Estructurada en Backends"
description: "Virtual threads eliminan la gestión manual de pools y simplifican la concurrencia bloqueante. Pattern matching con tipos sellados garantiza exhaustividad en el modelado de estados. Structured concurrency y scoped values (preview) corrigen la fragilidad de CompletableFuture y ThreadLocal."
date: 2026-06-18
tags: ["java", "concurrency"]
issue: 25
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
reviewer: "minimax-m3"
quiz:
  - question: "¿Cuál es una limitación clave de los virtual threads?"
    options:
      - "Mejoran la latencia de peticiones individuales"
      - "No sustituyen backpressure ni modelos de streaming reactivo"
      - "Añaden paralelismo real para tareas CPU-bound"
      - "Eliminan la necesidad de hacer thread dumps"
    correct: 1
    explanation: "Los virtual threads simplifican la concurrencia bloqueante pero no proporcionan backpressure ni ayudan en carga CPU-bound. La latencia individual no mejora y los thread dumps siguen existiendo, aunque con más hilos."
  - question: "¿Qué aporta el pattern matching con sealed records frente a cadenas de instanceof?"
    options:
      - "Mayor rendimiento en runtime"
      - "Exhaustividad verificada por el compilador"
      - "Serialización JSON automática"
      - "Bytecode más compacto"
    correct: 1
    explanation: "Sealed interfaces + records permiten al compilador comprobar que se manejan todos los casos. El rendimiento, la serialización y el tamaño del bytecode no son beneficios directos."
  - question: "¿Qué hace ShutdownOnFailure en StructuredTaskScope?"
    options:
      - "Reintenta automáticamente las subtareas fallidas"
      - "Cancela las subtareas restantes cuando una falla"
      - "Ejecuta las subtareas de forma secuencial"
      - "Aumenta la prioridad de los virtual threads"
    correct: 1
    explanation: "ShutdownOnFailure cancela el resto de forks cuando uno falla. No reintenta, no secuencializa y no afecta a la prioridad de los hilos."
---

Un servicio concreto como hilo conductor
------------------------------------------

Imaginemos `OrderService`, responsable de procesar un pedido: validar inventario, ejecutar el cobro y coordinar el envío. En Java 17, una implementación típica usa un `ExecutorService` con pool fijo, `CompletableFuture` para las llamadas paralelas, `ThreadLocal` para propagar el traceId, y lógica de control con `instanceof` y casts.

```java
public class OrderServiceJava17 {
    private final ExecutorService pool = Executors.newFixedThreadPool(20);
    private final ThreadLocal<String> traceId = new ThreadLocal<>();

    public OrderResult process(Order order) {
        try {
            traceId.set(order.traceId());
            var inventory = CompletableFuture.supplyAsync(() -> checkInventory(order), pool);
            var payment = CompletableFuture.supplyAsync(() -> processPayment(order), pool);
            var shipment = CompletableFuture.supplyAsync(() -> arrangeShipment(order), pool);

            CompletableFuture.allOf(inventory, payment, shipment).join();

            if (order.getState() instanceof Pending) {
                Pending p = (Pending) order.getState();
                // ...
            } else if (order.getState() instanceof Confirmed) {
                // ...
            }
            // riesgo: olvidar un estado, sin aviso del compilador
            return new OrderResult(true);
        } finally {
            traceId.remove();
        }
    }
}
```

El diseño obliga a dimensionar el pool, mezcla lógica de negocio con gestión de hilos, y la cancelación o errores en una de las ramas no se propagan limpiamente a las otras. `ThreadLocal` exige limpieza manual y no escala con hilos virtuales. El modelado de estados mediante `instanceof` es frágil y no garantiza exhaustividad.

Virtual threads: cuándo simplifican y cuándo no
-----------------------------------------------

Los virtual threads (finales en Java 21) mapean un gran número de hilos ligeros a unos pocos hilos de plataforma. Una llamada bloqueante (I/O, `Thread.sleep`) libera el hilo de plataforma subyacente, permitiendo que otro virtual thread lo use. Esto elimina la necesidad de pools dimensionados a mano y de delegar bloqueos a `CompletableFuture`.

Refactorizar `OrderService` con virtual threads es directo: un virtual thread por petición, código bloqueante secuencial o paralelo con `ExecutorService` virtual.

```java
public class OrderServiceVirtual {
    private static final ScopedValue<String> traceId = ScopedValue.newInstance();

    public OrderResult process(Order order) throws Exception {
        return ScopedValue.where(traceId, order.traceId()).call(() -> {
            try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
                var inventory = scope.fork(() -> checkInventory(order));
                var payment   = scope.fork(() -> processPayment(order));
                var shipment  = scope.fork(() -> arrangeShipment(order));

                scope.join().throwIfFailed();
                return new OrderResult(true);
            }
        });
    }
}
```

El código usa `ScopedValue` y `StructuredTaskScope` (aún preview/incubator) para ilustrar el diseño al que se tiende, pero el punto central es que con virtual threads podemos escribir `checkInventory` como un método bloqueante sin saturar el pool. El dimensionamiento desaparece.

**Simplificación real:** servicios I/O-bound con muchas operaciones bloqueantes concurrentes (REST, JDBC, mensajería) ganan un modelo de concurrencia sencillo, sin `CompletableFuture` ni callbacks. La latencia de peticiones individuales no mejora, pero el throughput se mantiene sin los clásicos cuellos de botella por agotamiento de hilos de plataforma.

**Límites claros:**

- No sustituyen backpressure ni streaming reactivo. Si el servicio necesita control de flujo extremo a extremo (WebFlux, RSocket), los virtual threads no ofrecen mecanismos nativos de backpressure; el modelo reactivo sigue siendo la herramienta adecuada.
- No aceleran tareas CPU-bound puras. El scheduler de virtual threads no añade paralelismo real para cálculos intensivos; ahí sigue siendo necesario un pool de plataforma o el ForkJoinPool.
- Pinning: bloques `synchronized` o llamadas JNI que no liberan el carrier thread provocan que un virtual thread acapare un hilo de plataforma, degradando el rendimiento. Es necesario revisar librerías (drivers JDBC antiguos, clientes HTTP) y reemplazar `synchronized` por `ReentrantLock` donde sea posible.
- Observabilidad: un thread dump tradicional puede contener miles de virtual threads, volviéndose ilegible. Java 21 introdujo eventos JFR (`jdk.VirtualThreadStart`, `jdk.VirtualThreadPinned`) y el comando `jcmd <pid> Thread.dump_to_file` para volcados en texto plano; Java 24 añadió `-format=json` para facilitar el análisis automatizado. Las herramientas de APM deben adaptarse.

Pattern matching y modelado de datos: de la comprobación manual a la exhaustividad
-----------------------------------------------------------------------------------

Java 21 finalizó pattern matching for switch y record patterns. Esto permite modelar los estados del pedido como una jerarquía sellada con records, y el compilador garantiza que se cubren todos los casos.

```java
sealed interface OrderState permits Pending, Confirmed, Shipped {}
record Pending(Instant since) implements OrderState {}
record Confirmed(String paymentId) implements OrderState {}
record Shipped(String trackingId) implements OrderState {}

// Uso en el servicio
String describe(OrderState state) {
    return switch (state) {
        case Pending(var since) -> "Pending since " + since;
        case Confirmed(var paymentId) -> "Confirmed with " + paymentId;
        case Shipped(var trackingId) -> "Shipped, tracking " + trackingId;
        // sin default: el compilador verifica exhaustividad
    };
}
```

El impacto en diseño va más allá de la sintaxis: el modelado con tipos algebraicos (sum types mediante sealed + records) se convierte en la opción natural. Desaparece la necesidad del visitor pattern y las comprobaciones manuales propensas a olvidos. Cuando se añade un nuevo estado, el compilador señala todos los puntos donde hay que manejarlo. Esto empuja a diseñar dominios con precisión y a eliminar estados inválidos por construcción.

Structured concurrency y scoped values: lifetimes y contexto bajo control
-------------------------------------------------------------------------

`CompletableFuture.allOf()` rompe la relación padre-hijo: si una tarea falla, las demás siguen ejecutándose a menos que se implemente cancelación manual. El manejo de errores se dispersa. `ThreadLocal` no se limpia automáticamente y, con virtual threads, su uso masivo genera presión de memoria y fugas si no se retira explícitamente.

Structured concurrency (preview en Java 24, finalizado en Java 25) confina las subtareas a un scope con un lifetime bien definido. `StructuredTaskScope` garantiza que al salir del bloque todas las tareas han terminado (o han sido canceladas). `ShutdownOnFailure` cancela las demás cuando una falla.

Scoped values (preview desde Java 21, finalizado en Java 25) proporcionan un contexto inmutable y heredable con ámbito léxico, sin los problemas de `ThreadLocal`: se limpian automáticamente al salir del scope, son seguros con virtual threads y no requieren código de limpieza manual.

En el ejemplo anterior, `ScopedValue` propaga el traceId y `StructuredTaskScope` coordina las tres llamadas: un fallo en el cobro cancela automáticamente las consultas de inventario y envío. El código es lineal y el razonamiento sobre concurrencia, local.

**Trade-offs reales:** al no ser finales, los frameworks (Spring, Quarkus) no ofrecen integración nativa. Migrar código asíncrono existente basado en `CompletableFuture` requiere reestructurar la lógica hacia bloques `try`-with-resources con scopes, lo que no es un reemplazo directo. En entornos de producción, conviene experimentar en rutas no críticas y esperar a que el ecosistema madure.

Migrar desde Java 17 o 21: recomendaciones prácticas
------------------------------------------------------

**Desde Java 17:** el primer paso es subir a 21 LTS. Virtual threads y pattern matching se pueden adoptar de inmediato con riesgo bajo. Virtual threads simplifican la capa de concurrencia sin cambiar APIs externas; pattern matching mejora el modelado sin romper compatibilidad. No es necesario reescribir servicios completos: basta con habilitar virtual threads en el pool de entrada (por ejemplo, `Executors.newVirtualThreadPerTaskExecutor()`) y empezar a usar switch exhaustivo en nuevos módulos.

**Desde Java 21:** structured concurrency y scoped values deben evaluarse en entornos de preproducción o en servicios internos. No reescriba servicios reactivos que funcionan correctamente; los virtual threads no reemplazan WebFlux si ya tiene sentido en su arquitectura. La decisión de migrar código asíncrono a structured concurrency debe basarse en la fragilidad real de la cancelación y el manejo de errores actual.

**Checklist de bloqueantes:**

- Verificar drivers JDBC: muchos drivers antiguos contienen bloques `synchronized` que causan pinning. Use versiones recientes o drivers específicos para virtual threads (por ejemplo, el driver de PostgreSQL 42.5+).
- Clientes HTTP: el `HttpClient` de Java 11+ funciona correctamente con virtual threads; librerías como OkHttp o Apache HttpClient pueden requerir revisión de `synchronized`.
- Librerías de MDC/tracing: si usan `ThreadLocal` para propagar contexto, evalúe migrar a `ScopedValue` cuando esté finalizado, o asegure limpieza explícita en hooks de finalización de petición.
- Observabilidad: active eventos JFR de virtual threads (`jdk.VirtualThreadPinned`, `jdk.VirtualThreadStart`) y configure dashboards para detectar pinning. Use `jcmd Thread.dump_to_file` con formato JSON para análisis automatizado.

Conclusión
----------

La ventana Java 21–25 no impone una revolución forzada, pero ofrece herramientas que atacan la complejidad accidental del diseño de backends. Virtual threads eliminan la gestión artesanal de pools y devuelven la concurrencia bloqueante a un modelo sencillo. Pattern matching transforma el modelado de datos en algo exhaustivo y seguro. Structured concurrency y scoped values, aunque aún en evolución, apuntan a un manejo de lifetimes y contexto que corrige décadas de patrones frágiles. El criterio está en adoptar lo que simplifica el diseño real del servicio hoy y posponer lo que aún requiere maduración del ecosistema.