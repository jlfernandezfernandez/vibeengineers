# Ctx

[![CI](https://github.com/jlfernandezfernandez/ctx/actions/workflows/ci.yml/badge.svg)](https://github.com/jlfernandezfernandez/ctx/actions/workflows/ci.yml)

Una píldora técnica al día. Para vibe coders que quieren entender qué pasa por debajo.

Cada día laborable, un artículo técnico concentrado sobre un tema propuesto y votado por el equipo. Un concepto, bien explicado, en ~5 minutos.

## Cómo funciona

1. Propón un tema con la plantilla de issue ["Proponer tema"](../../issues/new/choose). El agente de **triaje** modera spam y convierte las notas en un briefing útil; ante la duda, decide una persona.
2. **Vota con 👍**: cada madrugada laborable (`04:30 UTC`) se elige el tema aceptado más votado (empate → el más antiguo; el label `priority` salta la cola).
3. El **writer** construye el artículo alrededor de una pregunta central y una tesis útil, decide la estructura, genera título, resumen y tags, y abre una PR antes de la revisión.
4. El **reviewer** evalúa código, rigor y legibilidad sobre esa PR: cada defecto es **bloqueante** (código incorrecto, dato falso o desactualizado, referencia inventada, concepto imprescindible ausente o texto demasiado superficial o disperso) o **sugerencia** (mejora no imprescindible).
   - Sin bloqueantes → merge automático (las sugerencias quedan como comentario), la issue se cierra con el link y la web se despliega.
   - Con bloqueantes → comenta "cambios solicitados" en la PR y se los devuelve al **writer**, que corrige y vuelve a revisión. Máximo `MAX_REVIEW_ROUNDS` correcciones para que el reviewer no saque pegas indefinidamente.
   - Si tras agotar las rondas siguen los bloqueantes, **la PR queda abierta** con la mejor versión y los defectos comentados: una PR de artículo abierta significa "decide un humano" (mergear publica, cerrar descarta). La cola no se bloquea: al día siguiente toca el siguiente tema.

Dos workflows invocan los entrypoints del pipeline Python: [`triage-topic.yml`](.github/workflows/triage-topic.yml) clasifica cada propuesta al abrirse y [`publish.yml`](.github/workflows/publish.yml) ejecuta la selección, redacción, revisión y publicación diaria. El pipeline valida contratos objetivos y gestiona GitHub; los agentes solo toman decisiones editoriales.

## Agentes

| Agente          | Modelo               | Qué hace                                                                                                                                       |
| --------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **Triaje**   | `LLM_TRIAGE_MODEL`   | Acepta, rechaza o escala propuestas y prepara el briefing |
| **Writer**   | `LLM_WRITER_MODEL`   | Desarrolla una tesis, genera título, resumen, tags y artículo; corrige feedback |
| **Reviewer** | `LLM_REVIEWER_MODEL` | Evalúa código, rigor, profundidad y legibilidad; devuelve defectos o sugerencias |

Cada agente tiene un único system prompt estático en `generator/src/article_generator/system_prompts/`. No se comparten ni interpolan reglas entre agentes. El writer y el reviewer usan modelos distintos para evitar que un modelo apruebe sus propios vicios.

El resto no son agentes: `pipeline.py` coordina rondas y GitHub; `article.py` valida Markdown, frontmatter y el máximo de tags.

El reviewer recibe extractos de hasta cinco referencias HTTPS del artículo.
`references.py` descarga un máximo de 200 KB por fuente y entrega hasta 2.500
caracteres de texto, con conexión y lectura limitadas. No sigue redirecciones
ni accede a direcciones privadas. Las fuentes inaccesibles se indican como
«No verificable»; el reviewer conserva la decisión editorial y puede pedir
evidencia. Un extracto parcial no demuestra por sí solo que una afirmación sea
correcta o falsa. No hay un agente adicional ni un buscador externo.

## Estructura

- `generator/` — generador Python (LLM agnóstico vía API OpenAI-compatible)
- `frontend/` — web Astro
- `.github/workflows/` — `triage-topic` (cura propuestas), `publish` (pipeline editorial), `ci` (tests y build)
- `frontend/src/data/tags.json` — taxonomía canónica: el writer reutiliza tags existentes siempre que encajen y el pipeline añade a la misma PR un tag nuevo solo cuando hace falta

Cada artículo lleva entre uno y tres tags que representen sus ejes centrales. Un tag solo se incluye
si alguien interesado en él agradecería encontrar el artículo; la taxonomía puede crecer como máximo
en un tag nuevo por artículo.

Los únicos labels requeridos por el producto son `triage`, `topic`, `priority`, `published` y `rejected`.

## Configuración (Actions)

### Providers

| Dónde    | Nombre                    | Valor actual                       |
| -------- | ------------------------- | ---------------------------------- |
| Secret   | `OLLAMA_API_KEY`          | API key de Ollama Cloud            |
| Variable | `OLLAMA_BASE_URL`         | `https://ollama.com/v1`            |
| Secret   | `OPENROUTER_API_KEY`      | API key de OpenRouter              |
| Variable | `OPENROUTER_BASE_URL`     | `https://openrouter.ai/api/v1`     |

### Agentes

| Dónde    | Nombre                          | Valor de ejemplo              |
| -------- | ------------------------------- | ----------------------------- |
| Variable | `AGENT_TRIAGE_PROVIDER`         | `openrouter`                  |
| Variable | `AGENT_TRIAGE_MODEL`            | `openai/gpt-5-nano`           |
| Variable | `AGENT_WRITER_PROVIDER`         | `ollama`                      |
| Variable | `AGENT_WRITER_MODEL`            | `deepseek-v4-pro`             |
| Variable | `AGENT_WRITER_JSON_PROVIDER`    | `openrouter`                  |
| Variable | `AGENT_WRITER_JSON_MODEL`       | `anthropic/claude-sonnet-4.6` |
| Variable | `AGENT_REVIEWER_PROVIDER`       | `openrouter`                  |
| Variable | `AGENT_REVIEWER_MODEL`          | `openai/gpt-5`                |
| Variable | `MAX_REVIEW_ROUNDS`             | `2`                           |

Cada agente apunta a un provider y modelo. El writer usa dos capabilities: chat libre (`AGENT_WRITER_*`) y JSON estructurado (`AGENT_WRITER_JSON_*`). OpenRouter se usa para tareas con `response_format: json_schema`; Ollama Cloud para texto libre.

## Desarrollo local

Con [Docker](https://docs.docker.com/get-docker/):

```bash
cp .env.example .env          # rellena GARAGE_RPC_SECRET y GARAGE_ADMIN_TOKEN: openssl rand -hex 32
docker compose up --build      # postgres, garage, backend (:8000), frontend (:4321)
```

- Frontend: http://localhost:4321
- Backend `/health`: http://localhost:8000/health

Garage arranca con el compose; la creación del bucket y keys de S3 se configura cuando el backend integre almacenamiento.

### Sin Docker

Generador (Python, requiere [uv](https://docs.astral.sh/uv/), `brew install uv`):

```bash
cd generator
uv run --extra dev pytest
```

Web (Astro, requiere [Node.js](https://nodejs.org) ≥ 22.12):

```bash
cd frontend
npm install
npm run dev
```

GitHub Pages compila `frontend/` con `SITE_URL=https://jlfernandezfernandez.github.io/ctx`.
La misma variable fija el origen canónico y el prefijo `/ctx` de enlaces y recursos.
En local se omite para servir desde `http://localhost:4321/`; en otro alojamiento
se usa su URL pública completa. El generador escribe artículos y tags en
`frontend/src/`, que es también la ruta que consume la web.

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md). La forma más útil: proponer buenos temas con notas de enfoque, y votar 👍 los que te interesan.
