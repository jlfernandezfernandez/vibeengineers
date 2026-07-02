---
title: "Kafka sin ZooKeeper: KRaft"
description: "KRaft elimina la dependencia de ZooKeeper mediante un quorum Raft interno que almacena metadatos en un topic compactado, reduciendo el failover del controller a segundos y escalando a millones de particiones. Unifica la seguridad bajo los mecanismos nativos de Kafka y admite modos combinado y dedicado, pero exige dimensionar correctamente los controllers para evitar timeouts en el quorum."
date: 2026-06-11
tags: ["kafka"]
issue: 4
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
reviewer: "minimax-m3"
quiz:
  - question: "¿Qué mecanismo usa KRaft para almacenar los metadatos del clúster?"
    options:
      - "ZooKeeper znodes"
      - "Un topic compactado interno __cluster_metadata"
      - "Una base de datos relacional externa"
      - "BookKeeper ledgers"
    correct: 1
    explanation: "KRaft replica los metadatos mediante Raft en un topic compactado interno. ZooKeeper ha sido eliminado, no usa base de datos externa y BookKeeper es la opción de Pulsar."
  - question: "¿Por qué en producción con mucho tráfico se recomiendan controllers dedicados en lugar del modo combinado?"
    options:
      - "El modo combinado requiere más espacio en disco"
      - "El tráfico de datos puede robar CPU al controller y provocar elecciones espurias"
      - "Los controllers dedicados tienen menos opciones de seguridad"
      - "El modo combinado no soporta SASL_SSL"
    correct: 1
    explanation: "En nodos combinados, la carga de datos compite con los heartbeats del quorum. Las otras opciones son incorrectas: la seguridad es igual y el disco no es el motivo."
  - question: "¿Qué configuración debe ser idéntica en todos los nodos de un quorum KRaft?"
    options:
      - "El node.id"
      - "La lista controller.quorum.voters"
      - "El advertised.listeners"
      - "La ruta metadata.log.dir"
    correct: 1
    explanation: "controller.quorum.voters debe ser exactamente igual y apuntar a hosts resolubles entre nodos. node.id es único por nodo, advertised.listeners varía y metadata.log.dir es local."
---

## El problema de la doble operación

Durante más de una década, Kafka delegó en ZooKeeper el registro de brokers, la configuración de topics, la asignación de particiones y el liderazgo de réplicas. Operar el clúster significaba mantener dos sistemas distribuidos con modelos de consistencia, protocolos (ZAB vs. Kafka) y seguridad distintos. En clústeres grandes, las sesiones con heartbeats provocaban falsos positivos y expulsiones de brokers.

El cuello de botella práctico estaba en el failover del controller. Cuando ZooKeeper elegía un nuevo controller, este recargaba todos los metadatos desde cero: una operación O(particiones) que en clústeres con más de 200 000 particiones tardaba decenas de segundos —tiempo durante el cual no se podían crear topics ni mover particiones. La seguridad también era dual: ACLs de ZooKeeper por un lado y SASL/TLS de Kafka por otro.

KRaft (Kafka Raft) elimina la dependencia, baja el failover a segundos y permite millones de particiones. Kafka 3.3 lo declaró listo para producción y **Kafka 4.0 ha retirado el modo ZooKeeper**: para cualquier versión actual, KRaft ya no es opcional.

## Cómo KRaft reemplaza a ZooKeeper

Un grupo de nodos *controller* forma un quorum Raft. Uno actúa como líder y los demás replican pasivamente un log de metadatos almacenado en un topic compactado interno, `__cluster_metadata`. Los brokers no participan en el quorum: obtienen los metadatos del líder por un RPC (Metadata Fetch) y se comportan como observadores del estado consensuado.

Hay dos modos de despliegue:

- **Combinado**: un mismo proceso es broker y controller. Práctico para clústeres pequeños y dev.
- **Dedicado**: los controllers viven en nodos separados sin servir tráfico de datos. Recomendado en producción con cientos de brokers, porque aísla el plano de control del de datos.

La seguridad se unifica bajo los mecanismos nativos de Kafka: SASL/SCRAM, mTLS o delegation tokens sobre listeners específicos. Se eliminan las ACLs de ZooKeeper, pero hay que definir listeners separados para el tráfico de controller y el de broker, cada uno con su propio protocolo.

Cada cambio de estado (crear topic, reassignment, cambio de config) se escribe como un registro en el log Raft. Los seguidores lo replican y lo aplican a una máquina de estados en memoria. Si el líder cae, un seguidor toma el relevo de inmediato porque ya tiene el estado completo: no hace falta reconstruir nada. Para evitar crecimiento ilimitado del log, el controller genera snapshots periódicas que serializan el estado actual y permiten truncar registros antiguos.

## El precio de empotrar el consenso

Kafka implementa su propio Raft en lugar de usar bibliotecas externas como Apache Ratis. El quorum se configura con voters fijos identificados por `node.id`: para tolerar F fallos hacen falta 2F+1 voters. Una entrada se considera comprometida cuando la mayoría la reconoce, y los timeouts aleatorios de elección evitan split-brain.

Los trade-offs operacionales son reales:

- **En modo combinado, un pico de tráfico de datos puede robar CPU al controller**, provocando timeouts de heartbeat y elecciones de líder espurias que degradan todo el clúster. Por eso en cargas altas se aíslan controllers dedicados.
- La configuración de listeners (por ejemplo, `SASL_PLAINTEXT` para brokers y `SASL_SSL` para controllers) requiere atención al detalle.
- Algunas configuraciones que en ZooKeeper se cambiaban en caliente con `kafka-configs` aún requieren rolling restart en KRaft.

Frente al failover O(particiones) del antiguo modelo, KRaft escala a varios millones de particiones validados en pruebas de Confluent. La migración desde ZooKeeper se hace con un modo *dual-write* documentado en [KIP-866](https://cwiki.apache.org/confluence/display/KAFKA/KIP-866+ZooKeeper+to+KRaft+Migration): se añaden controllers KRaft al clúster existente, se sincronizan los metadatos y finalmente se corta la dependencia, sin downtime.

A diferencia de Apache Pulsar, que delegó los metadatos en BookKeeper, Kafka empotra el consenso dentro de su propio proceso. Es una filosofía de autosuficiencia: el broker reutiliza red, seguridad y protocolos propios sin introducir otra dependencia externa.

## Configuración esencial de un nodo

Lo mínimo que define un nodo KRaft son sus roles, el listener de quorum y la lista de voters:

```properties
node.id=1
process.roles=broker,controller

listeners=BROKER://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
advertised.listeners=BROKER://kafka1.example.com:9092
inter.broker.listener.name=BROKER
controller.listener.names=CONTROLLER
listener.security.protocol.map=BROKER:SSL,CONTROLLER:SSL

# Voters: idéntica en todos los nodos del quorum
controller.quorum.voters=1@kafka1.example.com:9093,2@kafka2.example.com:9093,3@kafka3.example.com:9093

log.dirs=/var/lib/kafka/data
metadata.log.dir=/var/lib/kafka/data/metadata
```

Antes del primer arranque, cada nodo debe formatear el directorio de metadatos con el mismo `cluster-id`:

```bash
kafka-storage format \
  --cluster-id="$CLUSTER_ID" \
  --config /etc/kafka/server.properties
```

Para inspeccionar el quorum en caliente:

```bash
kafka-metadata-quorum --bootstrap-server localhost:9092 describe --status
# NodeId  Host    Port  Status
# 1       kafka1  9093  Leader
# 2       kafka2  9093  Follower
# 3       kafka3  9093  Follower
# LeaderEpoch: 5, HighWatermark: 142
```

Y para añadir o retirar voters dinámicamente, `add-controller --node-id N --host H --port P` y `remove-controller --node-id N`, siempre manteniendo mayoría durante la transición.

Los clientes no necesitan ningún cambio. El `AdminClient` sigue funcionando igual; internamente, el protocolo Metadata Fetch contacta al controller activo. `describeCluster().controller()` devuelve ahora el nodo controller, igual que antes apuntaba al broker controller ZooKeeper-elegido.

## Errores frecuentes

- **`controller.quorum.voters` inconsistente.** Debe ser idéntica en todos los nodos y apuntar al listener CONTROLLER de cada voter. Usar `localhost` en lugar de hostnames resolubles entre nodos rompe la comunicación.
- **Mezclar config ZooKeeper y KRaft fuera del proceso de migración.** Conviven solo en modo *dual-write* con voters KRaft añadidos al clúster ZK existente; arrancar con `zookeeper.connect` y `process.roles` a la vez fuera de ese flujo provoca split-brain.
- **Nodos combinados sin recursos garantizados.** Con tráfico alto, los heartbeats del quorum compiten con I/O de datos. En clústeres grandes, controllers dedicados con CPU y red aisladas.
- **`controller.listener.names` desalineado con `listeners`.** El nombre debe coincidir literalmente y su protocolo en `listener.security.protocol.map` ser coherente con la config de seguridad (declarar `SSL` exige keystores).
- **Olvidar `kafka-storage format`.** Sin formatear, el proceso se niega a arrancar. En contenedores se scripta en el entrypoint; en despliegues manuales se olvida.
- **Pérdida del quorum.** Si cae la mayoría de controllers, el plano de control queda indisponible. Hay que monitorizar (JMX Raft, `kafka-metadata-quorum describe --status`) y conocer el procedimiento: `remove-controller` del nodo perdido y `add-controller` de su reemplazo, sin romper la mayoría.

## Para saber más

- [Apache Kafka Documentation: KRaft](https://kafka.apache.org/documentation/#kraft)
- [KIP-500: Replace ZooKeeper with a Self-Managed Metadata Quorum](https://cwiki.apache.org/confluence/display/KAFKA/KIP-500%3A+Replace+ZooKeeper+with+a+Self-Managed+Metadata+Quorum)
- [KIP-866: ZooKeeper to KRaft Migration](https://cwiki.apache.org/confluence/display/KAFKA/KIP-866+ZooKeeper+to+KRaft+Migration)
- [Confluent: Apache Kafka Without ZooKeeper: The KRaft Era](https://www.confluent.io/blog/kafka-without-zookeeper-kraft-era/)
