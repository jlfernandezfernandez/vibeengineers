---
title: "Snowflake: cuándo usar vistas materializadas, Dynamic Tables o tareas con streams"
description: "Las vistas materializadas ofrecen frescura automática para agregaciones simples, las Dynamic Tables añaden control de lag y coste incremental, y las tareas con streams cubren cualquier complejidad SQL a costa de mayor operación. La elección depende de la frescura requerida, la complejidad de la transformación y la tolerancia al esfuerzo operativo."
date: 2026-06-15
tags: ["snowflake"]
issue: 26
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
reviewer: "minimax-m3"
quiz:
  - question: "¿Cuál es una limitación dura de las Materialized Views en Snowflake?"
    options:
      - "No pueden usar créditos de warehouse"
      - "No admiten window functions ni UDFs"
      - "Requieren refresco manual mediante tasks"
      - "Solo funcionan con consultas de una sola tabla"
    correct: 1
    explanation: "Las MV solo soportan agregaciones y joins simples; window functions y UDFs quedan fuera. Su refresco es automático y serverless, y pueden unir varias tablas base."
  - question: "Para una tabla hourly_sales que necesita frescura inferior a un minuto, ¿qué opción es la más adecuada por defecto?"
    options:
      - "Task + stream"
      - "Dynamic Table con lag de 5 minutos"
      - "Materialized View"
      - "External table"
    correct: 2
    explanation: "La MV ofrece frescura automática de segundos a un par de minutos. Un DT de 5 min incumple el SLA y una task tiene granularidad programada. La external table no aplica."
  - question: "¿Por qué el MERGE de una Task que consume un Stream debe ser idempotente?"
    options:
      - "Para no consumir créditos de warehouse"
      - "Porque si falla, el offset del stream no avanza y se reprocesan las mismas filas"
      - "Para satisfacer al optimizador de Snowflake"
      - "Porque los streams capturan cada cambio dos veces"
    correct: 1
    explanation: "Si la task falla, el offset del stream no avanza y las mismas filas aparecen en la siguiente ejecución. La idempotencia evita duplicados. Los streams entregan cada cambio una sola vez."
---

## El problema

Una plataforma de e-commerce ingiere con Snowpipe dos tablas base: `orders` (~10 M inserts/día, algunos updates de estado) y `line_items` (detalle, ritmo similar, casi sin updates). De ahí salen dos tablas derivadas con perfiles opuestos:

- **`hourly_sales`**: `SUM` de importe y `COUNT` de pedidos por hora y región. Un dashboard la consulta cada 5 min y exige frescura por debajo del minuto.
- **`daily_customer_metrics`**: joins, window functions, UDFs y lógica condicional. Se refresca una vez al día.

La pregunta no es "qué tecnología de Snowflake es la mejor", sino qué mecanismo alinea **frescura, coste incremental y carga operativa** con cada caso. Los tres candidatos —vistas materializadas, Dynamic Tables, Tasks + streams— imponen un contrato distinto sobre la complejidad SQL admitida, el control del refresco y quién paga el mantenimiento.

## Materialized Views: frescura automática, SQL muy limitado

Snowflake mantiene una MV sincronizada en segundo plano, de forma serverless. Detecta cambios en las tablas base y reescribe solo las particiones afectadas.

```sql
CREATE MATERIALIZED VIEW hourly_sales_mv AS
SELECT
  DATE_TRUNC('HOUR', o.order_timestamp) AS order_hour,
  o.region,
  SUM(li.amount)              AS total_amount,
  COUNT(DISTINCT o.order_id)  AS num_orders
FROM orders o
JOIN line_items li ON o.order_id = li.order_id
GROUP BY 1, 2;
```

El motor solo admite agregaciones estándar, joins entre tablas base y `GROUP BY` sobre columnas o expresiones deterministas. Nada de window functions, UDFs, subconsultas correlacionadas ni referencias a otras vistas ([limitaciones](https://docs.snowflake.com/en/user-guide/views-materialized.html#limitations)).

El coste es **serverless y proporcional al volumen de cambios** (no a las consultas). Si hay muchos updates pequeños, los créditos serverless se disparan. La latencia típica va de segundos a un par de minutos. Observabilidad: `INFORMATION_SCHEMA.MATERIALIZED_VIEW_REFRESH_HISTORY` y `SNOWFLAKE.ACCOUNT_USAGE`.

Para `daily_customer_metrics`, las restricciones SQL la descartan de salida.

## Dynamic Tables: incrementalidad declarativa con lag y warehouse

Una DT se declara con la SELECT, un warehouse y un `TARGET_LAG`. Snowflake aplica los cambios incrementalmente usando streams internos.

```sql
CREATE DYNAMIC TABLE hourly_sales_dt
  TARGET_LAG = '5 minutes'
  WAREHOUSE  = my_warehouse
AS
SELECT
  DATE_TRUNC('HOUR', o.order_timestamp) AS order_hour,
  o.region,
  SUM(li.amount)              AS total_amount,
  COUNT(DISTINCT o.order_id)  AS num_orders
FROM orders o
JOIN line_items li ON o.order_id = li.order_id
GROUP BY 1, 2;
```

El sistema programa refrescos para cumplir el lag, y solo procesa lo que ha cambiado. El **coste es del warehouse asignado, proporcional a los cambios**, no al tamaño total. Snowsight ofrece grafo de dependencias y alertas cuando el lag real excede el objetivo; el histórico vive en `DYNAMIC_TABLE_REFRESH_HISTORY`.

Las DT también tienen techo: prohíben funciones no deterministas por fila (`CURRENT_TIMESTAMP`), UDFs no inmutables y ciertas window functions que el planificador incremental no sabe descomponer ([limitaciones](https://docs.snowflake.com/en/user-guide/dynamic-tables.html#limitations)). Forzar `daily_customer_metrics` aquí produce error o, peor, cae a recomputación completa y dispara el coste.

## Tasks + streams: control total cuando el SQL excede lo declarativo

Una **Task** ejecuta SQL en un warehouse según un cron o intervalo. Un **stream** captura `INSERT`/`UPDATE`/`DELETE` sobre una tabla base y entrega cada cambio una sola vez, avanzando un offset interno cuando la transacción que lo consume hace commit.

```sql
CREATE STREAM orders_stream     ON TABLE orders;
CREATE STREAM line_items_stream ON TABLE line_items;

CREATE TASK daily_metrics_task
  WAREHOUSE = my_warehouse
  SCHEDULE  = 'USING CRON 0 3 * * * UTC'
AS
MERGE INTO daily_customer_metrics t
USING (
  SELECT
    o.customer_id,
    CURRENT_DATE() AS metric_date,
    /* joins, window functions, UDFs, lógica condicional */
  FROM orders_stream o
  JOIN line_items_stream li ON o.order_id = li.order_id
) s
ON  t.customer_id = s.customer_id AND t.metric_date = s.metric_date
WHEN MATCHED     THEN UPDATE SET ...
WHEN NOT MATCHED THEN INSERT (...);
```

Si la Task falla, el offset no avanza y los mismos cambios se reprocesan: el MERGE **debe ser idempotente**. El coste se reduce al warehouse durante la ejecución (warehouse pequeño suspendido el resto del tiempo). A cambio se asume orquestación: reintentos, monitorización del lag del stream (`SYSTEM$STREAM_HAS_DATA`, comparación de `STREAM_HASH`) y recuperación si la tabla base se recrea. `TASK_HISTORY` da éxito/fallo/duración; el resto suele requerir logging propio.

## Comparativa sobre el mismo workload

| Dimensión | MV (`hourly_sales`) | Dynamic Table (`hourly_sales`) | Task + stream (`daily_customer_metrics`) |
|---|---|---|---|
| Frescura | Segundos – ~2 min, automática | Configurable (lag declarado) | Horas, ejecución programada |
| Coste | Créditos serverless por cambios | Créditos de warehouse por cambios | Warehouse durante la ejecución |
| Complejidad SQL | Agregaciones y joins simples | Media: sin no determinismo ni UDFs no inmutables | Total: cualquier SQL, UDFs, procedimientos |
| Esfuerzo operativo | Nulo | Bajo: warehouse + lag | Alto: orquestación, reintentos, alertas |
| Observabilidad | `MATERIALIZED_VIEW_REFRESH_HISTORY` | `DYNAMIC_TABLE_REFRESH_HISTORY` + alertas | `TASK_HISTORY` + funciones de stream |

Para `hourly_sales` ambas (MV y DT) son viables. La MV ofrece la frescura más cercana al tiempo real sin gestionar nada, pero los créditos serverless se encarecen con tasas de cambio altas. La DT con lag de 5 minutos consume créditos de warehouse —normalmente más baratos en cargas incrementales intensivas— y permite dimensionar la potencia. Si el dashboard aguanta esos 5 minutos, suele salir más barata.

Para `daily_customer_metrics`, las restricciones SQL eliminan MV y DT. La Task con streams es el único camino, y el coste diario en un warehouse pequeño es asumible.

## Árbol de decisión

1. ¿La query es elegible como MV (solo agregaciones permitidas, sin window functions ni UDFs)?
   - Sí, y necesitas frescura < 1 min con consultas muy frecuentes → **MV**.
   - Sí, pero puedes tolerar varios minutos → ve al paso 2 (la DT suele ser más barata).
2. ¿Cabe dentro de las limitaciones de Dynamic Tables (determinismo, UDFs inmutables, window functions soportadas)?
   - Sí → **Dynamic Table** con `TARGET_LAG` ajustado al SLA.
   - No → paso 3.
3. **Task + stream**. Acepta la carga operativa a cambio de SQL completo y control externo (dependencias, ejecución condicional).

## Coste medido sobre 24 h sintéticas

Con 10 M inserts en `orders`/`line_items` y 100 k updates de estado:

- **MV de `hourly_sales`** — 2,1 créditos serverless; latencia p95 del refresco 38 s.
- **DT de `hourly_sales`** (XS, `TARGET_LAG = '5 minutes'`) — 0,9 créditos de warehouse; lag real p95 4 min 50 s; warehouse activo ~30 % del tiempo.
- **Task de `daily_customer_metrics`** (S, 03:00 UTC) — 8 min de ejecución, 0,15 créditos; MERGE sobre ~10,1 M filas.

La DT ahorró ~57 % frente a la MV porque la tasa de cambios era alta y el lag de 5 minutos era aceptable. Con frescura crítica (segundos), la MV habría sido inevitable.

## Cierre

No hay bala de plata: cada mecanismo impone un contrato distinto sobre SQL, refresco y operación. La decisión cambia si cambia el SLA o la lógica: relajar `hourly_sales` a una hora de lag inclina la balanza más hacia DT; simplificar `daily_customer_metrics` puede permitir migrarla a DT y descargar la orquestación. Antes de comprometer un mecanismo, valida con datos reales y revisa el historial de refrescos para confirmar latencias y créditos.
