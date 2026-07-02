---
title: "Project Reactor: el paradigma reactivo en Java"
description: "Project Reactor es una librería para construir aplicaciones asíncronas y no bloqueantes en la JVM, implementando la especificación Reactive Streams. Resuelve el cuello de botella del modelo hilo-por-petición al usar un número reducido de hilos para manejar alta concurrencia I/O, gracias a flujos reactivos con backpressure. Usar cuando se requiera bajo consumo de recursos y alta escalabilidad, pero no si la lógica es inherentemente bloqueante o la depuración de pipelines reactivos añade una complejidad injustificada."
date: 2026-06-10
tags: ["java", "reactive"]
issue: 1
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
quiz:
  - question: "¿Cuál es el beneficio principal del backpressure en Reactive Streams?"
    options:
      - "Permite ejecutar operaciones bloqueantes dentro de pipelines reactivos"
      - "El consumidor controla el ritmo de emisión sin bloquearse"
      - "Aumenta automáticamente el tamaño del pool de hilos"
      - "Elimina la necesidad de llamar a subscribe()"
    correct: 1
    explanation: "Backpressure permite al suscriptor pedir datos con request(n) a su propio ritmo, evitando saturación. Bloquear dentro del pipeline destruye la escalabilidad, el tamaño del pool no depende de backpressure y Mono/Flux siguen siendo lazy, por lo que subscribe sigue siendo necesario."
  - question: "¿Qué Scheduler de Reactor se usa para envolver APIs bloqueantes heredadas?"
    options:
      - "Schedulers.parallel()"
      - "Schedulers.single()"
      - "Schedulers.boundedElastic()"
      - "Schedulers.immediate()"
    correct: 2
    explanation: "boundedElastic está pensado para APIs bloqueantes legacy. parallel es para computación, single para tareas secuenciales e immediate ejecuta en el hilo actual."
  - question: "¿Qué ocurre si un Flux.create ignora la demanda downstream?"
    options:
      - "El productor se pausa automáticamente"
      - "La cadena pasa de lazy a eager"
      - "Puede lanzar OverflowException o crecer la memoria sin control"
      - "Se activa un buffer de backpressure por defecto"
    correct: 2
    explanation: "Sin respetar sink.requestedFromDownstream() ni configurar una estrategia de saturación, las emisiones sobrantes provocan OverflowException o buffering descontrolado. La pereza y la pausa automática son ideas incorrectas."
---

## Cuando añadir más hilos deja de ayudar

En el modelo hilo-por-petición, cada request ocupa un hilo del pool hasta que toda la lógica termina, incluyendo las llamadas a base de datos o a servicios externos. Mientras el hilo espera I/O, queda bloqueado consumiendo memoria de pila sin hacer trabajo útil. Si el pool tiene 200 hilos y todos esperan respuestas remotas, la aplicación deja de aceptar conexiones aunque la CPU esté ociosa.

Aumentar el pool no escala: más hilos implican más memoria, más cambios de contexto y contención. En microservicios intensivos en I/O —que orquestan llamadas a otros servicios, BD, caches y colas— el resultado es bajo throughput y alta latencia en percentiles altos, con CPU infrautilizada.

La asincronía rompe ese acoplamiento: el hilo registra una continuación y vuelve al pool. Cuando llega el dato, otro hilo (o el mismo) reanuda el procesamiento. El paradigma reactivo lleva esa idea a un modelo de flujos push: el productor empuja datos al consumidor cuando están listos. La especificación [Reactive Streams](https://www.reactive-streams.org/) define el contrato mínimo entre `Publisher` y `Subscriber`, con un control de flujo no bloqueante llamado *backpressure*. Project Reactor es su implementación de referencia en la JVM y el núcleo reactivo de todo el ecosistema Spring.

## `Mono`, `Flux` y el contrato de backpressure

Reactor expone dos tipos: `Mono<T>` (0 ó 1 elementos) y `Flux<T>` (0 a N). Ambos son *lazy*: no ocurre nada hasta que alguien se suscribe. A diferencia de `CompletableFuture`, que es eager y no maneja backpressure, un `Mono`/`Flux` se compone declarativamente mediante operadores (`map`, `flatMap`, `filter`, `zip`...) que envuelven al `Publisher` anterior y solo se activan en la suscripción.

El elemento diferenciador es el *backpressure*. Al suscribirse, el `Subscriber` invoca `request(n)`; el `Publisher` no puede emitir más de lo solicitado. Tras procesar un elemento, el `Subscriber` pide más con otra llamada a `request`. Así el consumidor controla el ritmo sin bloquearse: si va lento, simplemente tarda en pedir.

```java
import reactor.core.publisher.Flux;
import reactor.core.publisher.BaseSubscriber;
import java.time.Duration;

public class ManualBackpressure {
    public static void main(String[] args) throws InterruptedException {
        Flux.interval(Duration.ofMillis(10))
            .subscribe(new BaseSubscriber<Long>() {
                @Override
                protected void hookOnSubscribe(Subscription s) {
                    request(5); // lote inicial
                }
                @Override
                protected void hookOnNext(Long value) {
                    try { Thread.sleep(200); } catch (InterruptedException e) {}
                    request(1); // pedir el siguiente solo cuando termine
                }
            });
        Thread.sleep(3000);
    }
}
```

El productor emite cada 10 ms pero el consumidor tarda 200 ms: gracias a `request` el productor no satura ni la memoria ni al consumidor.

## Qué ocurre debajo del pipeline

**Operadores como decoradores.** `Flux.just(1,2,3).map(x->x*2).filter(x->x>2)` no transforma listas: cada operador devuelve un nuevo `Publisher` que envuelve al anterior. Reactor aplica *operator fusion* (macro y micro) para evitar objetos intermedios, dejando cadenas `map`/`filter` casi al coste de un `for`.

**Schedulers.** Los cambios de hilo se controlan con `Scheduler`:

- `Schedulers.parallel()`: pool fijo del tamaño de las CPU, para cómputo. Nunca para I/O bloqueante.
- `Schedulers.boundedElastic()`: pool elástico con tope, pensado para envolver APIs bloqueantes legacy (JDBC, clientes síncronos).
- `Schedulers.single()`: un hilo para tareas secuenciales.

En Spring WebFlux el runtime es Netty: sus event loops manejan las conexiones sin bloquearse y Reactor encadena el trabajo sobre esos hilos, eliminando los pools grandes.

**Estrategias ante saturación.** Si el productor va por delante de la demanda y no hay buffer, Reactor lanza `OverflowException`. Para controlarlo: `onBackpressureBuffer(maxSize)`, `onBackpressureDrop()`, `onBackpressureLatest()` u `onBackpressureError()`. `limitRate(prefetch)` afina el tamaño de lote que pide un operador intermedio. Y operadores como `flatMap(mapper, concurrency, prefetch)` exponen explícitamente cuántas suscripciones internas pueden vivir a la vez: sin ajustarlos, un flujo rápido satura la memoria en silencio.

**Reactor vs. `CompletableFuture`.** `CompletableFuture` ofrece composición asíncrona pero no soporta múltiples elementos, no tiene backpressure y su manejo de errores es pobre. Reactor expresa reintento, timeout y fallback en línea:

```java
Mono.just(request)
    .flatMap(this::remoteCall)
    .timeout(Duration.ofSeconds(2))
    .retryWhen(Retry.backoff(3, Duration.ofMillis(100)))
    .onErrorResume(t -> Mono.just(fallbackResponse));
```

## Dónde suele romperse el modelo

**Bloquear dentro del pipeline.** Un `Thread.sleep` o una llamada JDBC dentro de un `map` ocupa un hilo del event loop esperando I/O y mata la escalabilidad. Solución: aislar en `subscribeOn(Schedulers.boundedElastic())` o, mejor, usar APIs reactivas (R2DBC, WebClient). [BlockHound](https://github.com/reactor/BlockHound) detecta automáticamente bloqueos en hilos no bloqueantes.

**Ignorar el backpressure.** Un `Flux.create` que llama a `sink.next()` sin mirar `sink.requestedFromDownstream()` desemboca en `OverflowException` o en crecimiento descontrolado de memoria. Hay que configurar siempre una estrategia explícita o respetar la demanda en la fuente.

**Confundir pereza con inactividad.** Sin `subscribe()` (o `block()` en tests) la cadena nunca se activa: Spring WebFlux se suscribe por ti, pero un main standalone termina sin ejecutar nada. La pereza también implica que cada nueva suscripción reejecuta los efectos secundarios; para evitarlo, `.cache()` o `.share()`.

**Stacktraces ilegibles.** Los operadores anónimos generan trazas crípticas. `Hooks.onOperatorDebug()` añade trazas de ensamblaje, pero su coste (5-10x) lo descarta en producción. Mejor `.checkpoint("etiqueta")` en puntos clave, y el agente `reactor-tools` para construir trazas bajo demanda con poco overhead.

**Fugas de recursos.** Para ficheros, conexiones o sockets, `Flux.using` no garantiza limpieza en caso de cancelación. `Flux.usingWhen` (y su variante `Mono`) define un *cleanup* asíncrono que se ejecuta en finalización, error o cancelación: imprescindible para no agotar pools.

## Para saber más

1. **[Reactor Core – Reference Guide](https://projectreactor.io/docs/core/release/reference/)**. La documentación oficial, con guías sobre operadores, schedulers y testing.
2. **[Reactive Streams Specification](https://www.reactive-streams.org/)**. Breve y esencial para entender el contrato de backpressure.
3. **[Reactive Spring – Josh Long](https://spring.io/reactive)**. Integración con Spring Boot y Spring Cloud.
4. **[Blog de Project Reactor](https://projectreactor.io/blog/)**. Decisiones técnicas y novedades del equipo.
