---
title: "Pydantic AI: Construir un agente tipado que pueda fallar bien"
description: "Pydantic AI modela los fallos de agentes de IA como estados tipados, permitiendo manejo de errores predecible, reintentos y observabilidad nativa. A diferencia de SDKs nativos o LangGraph, cada interacción produce un resultado tipado que el programa puede inspeccionar, facilitando pruebas y resiliencia en producción."
date: 2026-06-17
tags: ["ai-agents"]
issue: 27
requestedBy: "jlfernandezfernandez"
writer: "deepseek-v4-pro"
reviewer: "minimax-m3"
quiz:
  - question: "¿Cómo representa Pydantic AI un fallo de validación en la salida del LLM?"
    options:
      - "Lanzando una excepción genérica de Python"
      - "Como parte del estado tipado RunResult"
      - "Devolviendo silenciosamente None"
      - "Registrando el error y reintentando indefinidamente"
    correct: 1
    explanation: "Pydantic AI modela el fracaso como datos dentro de RunResult, no como excepciones, para que el llamador inspeccione y decida. Devolver None en silencio o reintentar para siempre no son comportamientos del framework."
  - question: "¿Cuál es la ventaja de devolver un ToolResponse con success=False en lugar de lanzar una excepción?"
    options:
      - "Ocultar los errores al LLM"
      - "Permitir que el LLM vea el fallo y decida el siguiente paso"
      - "Evitar la validación de Pydantic"
      - "Reducir el consumo de tokens"
    correct: 1
    explanation: "Codificar el fallo como dato mantiene al LLM en el bucle: puede reintentar, cambiar de herramienta o escalar. Ocultar errores o saltar la validación iría contra el diseño del framework."
  - question: "¿Qué utilidad de Pydantic AI permite pruebas unitarias deterministas sin llamar a modelos reales?"
    options:
      - "logfire.instrument_pydantic_ai"
      - "TestModel con custom_output_text"
      - "deps_type injection"
      - "ModelRetry"
    correct: 1
    explanation: "TestModel permite inyectar respuestas deterministas, incluidas salidas malformadas. logfire sirve para observabilidad, deps_type para inyectar dependencias y ModelRetry para reintentos en runtime."
---

Los agentes de IA en producción tropiezan con una realidad incómoda: los LLM son estocásticos. Un *tool call* malformado, un JSON que no respeta el esquema o una alucinación en los parámetros descarrila el flujo sin previo aviso. Los SDK nativos delegan en el desarrollador detectar y recuperar el fallo —*try/except* frágiles, reintentos manuales, sin contrato programático—. Frameworks como LangGraph ofrecen grafos potentes pero opacos: el error se propaga por nodos sin representación uniforme y depurar es arqueología.

Pydantic AI cambia el contrato: en vez de tratar los fallos como excepciones, los modela como **estados tipados** que el programa inspecciona y maneja con lógica ordinaria. Cada interacción produce un valor predecible. Esa previsibilidad es la base para sistemas testables, observables y resilientes.

## Modelar éxito y fracaso con tipos

Se define la salida esperada con un modelo Pydantic. El *runtime* inyecta el esquema en la llamada y valida la respuesta automáticamente.

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class WeatherReport(BaseModel):
    temperature_celsius: float
    conditions: str
    humidity_percent: float

weather_agent = Agent(
    "openai:gpt-4o",
    output_type=WeatherReport,
    system_prompt="Devuelve un informe meteorológico estructurado."
)
```

Si el modelo omite `humidity_percent` o asigna un string a `temperature_celsius`, Pydantic AI no lanza una excepción genérica: el `RunResult` que retorna `weather_agent.run()` encapsula el fallo de validación como parte de su estado. El desarrollador decide reintentar, derivar a un *fallback* o devolver un resultado parcial.

`RunResult` es el contenedor central: expone `data` (modelo validado, o `None` si falló), `all_messages()` (historial completo, incluidos *tool calls*) y `usage()` (tokens y peticiones). Si `data` es `None` hay que decidir qué hacer; `mypy`/`pyright` lo recuerdan en tiempo de desarrollo. Esa disciplina —el caso de fallo es visible en la firma— es la diferencia con un SDK que devuelve `str` y espera lo mejor.

Las dependencias del agente —cliente HTTP, conexión a base de datos, *feature flags*— se inyectan vía `deps_type` y se reciben en cada herramienta a través de `RunContext`. En producción se pasa el cliente real; en pruebas, un doble. El código del agente no cambia entre entornos.

## Herramientas que fallan bien

Las herramientas son la principal fuente de errores: APIs caídas, *rate limiting*, parámetros inválidos. Lanzar excepciones rompe el flujo del LLM. El patrón es devolver un modelo que encapsule éxito y fracaso en el mismo tipo.

```python
from pydantic import BaseModel
from pydantic_ai import RunContext
import httpx

class ToolResponse(BaseModel):
    success: bool
    data: str | None = None
    error_type: str | None = None
    error_message: str | None = None

async def search_knowledge_base(
    ctx: RunContext[SupportDeps], query: str
) -> ToolResponse:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.empresa.com/kb/search",
                params={"q": query},
                headers={"Authorization": f"Bearer {ctx.deps.api_key}"},
                timeout=5.0,
            )
            response.raise_for_status()
            return ToolResponse(success=True, data=response.json()["answer"])
    except httpx.TimeoutException:
        return ToolResponse(success=False, error_type="timeout")
    except httpx.HTTPStatusError as e:
        return ToolResponse(
            success=False,
            error_type="upstream_error",
            error_message=str(e.response.status_code),
        )
```

El agente recibe el `ToolResponse` en el historial. Si `success=False`, el LLM puede reintentar, cambiar de herramienta o pedir ayuda al usuario. Para errores que deben forzar reintento inmediato, la herramienta lanza `ModelRetry` y el *runtime* repite la llamada al modelo con el mensaje de error.

Codificar el fracaso como dato —en lugar de como excepción— tiene una consecuencia práctica: el contrato es uniforme. Cualquier herramienta, sea una llamada HTTP o una consulta SQL, devuelve la misma forma. El LLM aprende a interpretar `error_type` como una señal, no como una sorpresa. Y el código humano que rodea al agente puede agregar métricas, alertas o lógica condicional sin envolver cada herramienta en `try`.

## Reintentos y *fallbacks* tipados

```python
support_agent = Agent(
    "openai:gpt-4o",
    deps_type=SupportDeps,
    output_type=SupportResponse,
    tools=[faq_lookup, kb_search, escalate_to_human],
    retries=2,
)
```

`retries` controla los reintentos automáticos cuando el modelo produce una salida que no pasa la validación. Para errores de herramienta, el LLM ve el `ToolResponse` fallido y decide: si el `error_type` es `"timeout"`, reintenta; si es `"upstream_error"` con 403, escala. El *system prompt* instruye el orden (FAQ → KB → humano) y, como cada herramienta devuelve el mismo tipo, no hace falta lógica de coordinación en código.

La distinción es clave: `retries` cubre la validación del *output* del agente (errores sistemáticos del *schema*); el LLM, viendo respuestas tipadas de herramientas, cubre los errores transitorios. Reintentar ciegamente en código suele quemar tokens; reintentar con contexto —"esta herramienta dio timeout, prueba otra"— suele resolver.

## Observabilidad y pruebas

`logfire.instrument_pydantic_ai()` expone cada llamada al modelo, cada *tool call* y cada validación como spans OpenTelemetry, con `error_type` y `error_message` como atributos. Construir un dashboard de tasa de fallos por tipo de error es directo, sin parsear logs.

Para pruebas, `TestModel` simula respuestas deterministas, incluidas salidas malformadas:

```python
from pydantic_ai.models.test import TestModel

test_model = TestModel()
agent = Agent("openai:gpt-4o", model=test_model, output_type=WeatherReport)

test_model.custom_output_text = '{"temperature_celsius": "veinte", "conditions": "soleado"}'

result = await agent.run("¿Qué tiempo hace en Madrid?")
assert result.data is None
assert result.usage().request_count > 1
```

Las pruebas son rápidas, repetibles y no consumen tokens reales. Los caminos de recuperación (fallback, escalación) se verifican como cualquier otra lógica.

## Trade-off frente a SDK nativos y LangGraph

Los **SDK nativos** dan acceso directo y nada más: el *function calling* devuelve JSON crudo, parsear es manual, los reintentos son *ad-hoc*. Frágil en producción.

**LangGraph** modela el flujo como grafo dirigido. Es potente para orquestaciones complejas, pero el estado de error vive en un diccionario opaco y la resiliencia se construye caso a caso.

**Pydantic AI** asume el coste de una capa de abstracción a cambio de un contrato uniforme: cada resultado es un tipo, cada error es un dato. Menos *boilerplate* que un SDK, menos opacidad que un grafo, a costa de menos control sobre flujos no lineales.

## El tipo como ancla

En un mundo de modelos no deterministas, los tipos son la base de sistemas confiables. Al hacer del éxito y el fracaso valores con esquema, Pydantic AI convierte fallos impredecibles en estados que se manejan con lógica ordinaria. Testabilidad, observabilidad y resiliencia no son extras: emergen del diseño. Incluso cuando el agente falla, falla bien.
