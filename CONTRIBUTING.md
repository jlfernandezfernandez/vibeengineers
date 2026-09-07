# Contribuir a Ctx

El flujo editorial completo (triaje → votos → writer → reviewer) está documentado en el [README](README.md).

## Proponer un tema (lo más valioso)

Abre una issue con la plantilla **"Proponer tema"** y vota con 👍 las aceptadas (label `topic`):

- El **título de la issue** identifica el tema. El writer decide el título final del artículo.
- Las **notas de enfoque** ayudan al agente de triaje a preparar el briefing: qué no entiendes, con qué compararlo, qué casos cubrir.
- Un colaborador puede añadir `priority` para saltar la cola.

## Contribuir código

Ver [README.md → Desarrollo local](README.md#desarrollo-local) para el setup.

1. Haz fork y abre una PR contra `main`.
2. Generador (`generator/`): Python, TDD. Ejecuta `pytest`; los tests deben pasar.
3. Web (`frontend/`): Astro. `npm run build` debe compilar.
4. Las PRs de forks **no** reciben secrets: no puedes (ni necesitas) probar contra el LLM real. Mockea como hacen los tests existentes.

Los system prompts viven como texto estático en `generator/src/article_generator/system_prompts/`.
Cada agente tiene un único rol; la orquestación y las reglas verificables pertenecen a código normal.
El writer asigna entre uno y tres tags que representen ejes centrales, reutiliza los existentes
cuando encajan y solo amplía `tags.json` con un tag nuevo cuando la taxonomía todavía no representa
uno de esos ejes.

## Lo que no se puede hacer

- Commitear keys o `.env` (está en `.gitignore`; los secrets viven en GitHub Actions Secrets).
- Editar artículos publicados en `frontend/src/content/blog/` a mano salvo errata clara.
- Lanzar workflows: solo colaboradores con permiso de escritura pueden ejecutar `workflow_dispatch`.
