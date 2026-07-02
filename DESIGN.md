# ctx v2 — Documento de diseño

> 2026-07-02

## Visión

ctx pasa de web estática pública a producto cerrado con cuentas: una píldora técnica al día más **Libros** — libros técnicos troceados por LLM en píldoras autocontenidas de ~5 minutos, con plan de lectura y quizzes. Acceso solo para usuarios activados por el administrador. Todo autohospedado.

## Qué muere, qué nace

| Hoy                                                                  | v2                                                   |
| -------------------------------------------------------------------- | ---------------------------------------------------- |
| Web estática en GitHub Pages                                         | Web Astro con login, desplegada en Coolify           |
| Contenido (artículos) en el repo, publicado por merge                | Contenido en Postgres                                |
| Propuestas = issues de GitHub, votos = 👍                             | Propuestas y votos dentro de la web                  |
| Pipeline en GitHub Actions (triaje/writer/reviewer sobre issues/PRs) | Pipeline en el backend (cron + LLM)                  |
| Repo público                                                         | Repo privado, solo código                            |
| Leídos en localStorage                                               | Estado por usuario en BBDD (quiz respondido = leído) |

## Arquitectura

```
                    jordixlab.com (subdominios temporales)
                              │
              ┌───────────────┴───────────────┐
              │        Coolify (mini PC)       │
              │                                │
   ctx.jordixlab.com          api.ctx.jordixlab.com
        │                            │
   ┌────┴─────┐              ┌───────┴────────┐        ┌──────────┐
   │ Frontend │ ── fetch ──▶ │    Backend     │ ─────▶ │ Postgres │
   │  Astro   │              │ Python+FastAPI │        ├──────────┤
   └──────────┘              └───────┬────────┘ ─────▶ │  Garage  │
                                     │                 └──────────┘
                     ┌───────────────┼───────────────┐
                 OpenRouter     Ollama Cloud       Resend
              (structured out)  (solo texto)      (emails)
```

- **Monorepo** privado: `frontend/` + `backend/`. En Coolify, dos aplicaciones apuntando al mismo repo con Base Directory distinto y watch paths para que un push solo rebuilde la app tocada; Postgres y Garage como recursos aparte.
- **Frontend**: Astro estático + fetch a la API con cookie de sesión.
- **Backend**: FastAPI. Cron interno para la selección diaria de tema. Procesado de libros con background tasks + estado en Postgres; un redeploy interrumpe el task, así que el procesado se reanuda desde `book_jobs`.
- **Ficheros (PDFs)**: Garage (S3-compatible). El backend habla S3 con `endpoint_url` configurable — mismo código en local y prod.
- **LLM**: OpenRouter cuando hace falta structured output; Ollama Cloud cuando basta texto plano.
- **Emails**: Resend. Requiere SPF/DKIM en jordixlab.com.

## Autenticación y roles

- **GitHub OAuth directo** desde FastAPI (`authlib`) + cookie de sesión firmada.
- Flujo de acceso: login con GitHub → cuenta creada en estado `pending` → pantalla "acceso pendiente" → admin activa desde `/admin` (con aviso por email a partir de la fase 5; antes, los pendientes se revisan en `/admin`).
- **Futuro multi-proveedor** (p. ej. Google): migración a tabla `identities` (user 1—N proveedores) + auto-link por email verificado. No se construye hasta que exista el segundo proveedor.
- **Roles**: `user` y `admin`. Admin inicial se define por username de GitHub en variable de entorno.
- **Admin** (rutas `/admin` en la misma web, gateadas por rol):
  - Activar/desactivar usuarios.
  - Aprobar/cerrar propuestas de artículo.
  - Revisar y corregir el troceado de capítulos de un libro.
  - Revisar píldoras y plan de lectura antes de publicar.

## Modelo de datos

| Tabla                | Contenido                                                                                |
| -------------------- | ---------------------------------------------------------------------------------------- |
| `users`              | auth_provider + auth_provider_id, username, email, role, status (pending/active), fechas |
| `proposals`          | tema propuesto, briefing, estado (pending/approved/rejected/published), autor            |
| `votes`              | user × proposal, único                                                                   |
| `articles`           | título, resumen, tags, cuerpo (markdown), quiz (JSON, 3 preguntas), fecha, proposal_id   |
| `user_article_state` | user × article: respuestas de quiz, leído, fecha                                         |
| `books`              | título, autor, key del PDF en Garage, estado del procesado, subido por                   |
| `chapters`           | book_id, orden, título, texto limpio, estado (pending/validated)                         |
| `pills`              | chapter_id, orden, título, cuerpo (~5 min), quiz (JSON, 2 preguntas), estado             |
| `book_jobs`          | estado del procesado por capítulo (para reanudar/depurar fallos)                         |
| `user_book_progress` | user × book: píldoras completadas                                                        |

El plan de lectura no es tabla: es la vista ordenada de capítulos → píldoras.

## Flujos

### Editorial (artículo diario)

1. Usuario propone tema en la web → triaje LLM prepara briefing → **admin aprueba o cierra**.
2. Propuestas aprobadas visibles en la web; usuarios votan.
3. Cron diario: elige la aprobada más votada (empate → más antigua).
4. Writer redacta → reviewer pasa correcciones → writer corrige → se publica.

### Libros

1. Admin sube PDF (libros sin copyright).
2. Ingesta: PDF → texto limpio → troceado en capítulos.
3. **Validación humana del troceado.**
4. Cada capítulo → LLM → píldoras autocontenidas de ~5 min con quiz.
5. **Validación humana de píldoras y plan.**
6. Libro completo procesado y validado → se publica.

### Lectura

- Artículo: leer → responder quiz de 3 preguntas → marcado como leído.
- Píldora de libro: mismo mecanismo con quiz de 2 preguntas → completada.
- Libro: plan de lectura como índice; progreso por píldora, cross-device.

## Web (páginas)

| Página                                 | Acceso                                                                                                   |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Home / landing                         | **Pública** (única): qué es ctx, teaser de últimos artículos (título+resumen), botón "entrar con GitHub" |
| Artículos (lista + detalle + quiz)     | Login                                                                                                    |
| Libros (lista + plan + píldora + quiz) | Login                                                                                                    |
| Proponer tema + votar                  | Login                                                                                                    |
| Cómo funciona                          | Se decide con el diseño de la landing (candidato a integrarse en ella)                                   |
| `/admin`                               | Solo admin                                                                                               |

## Desarrollo local

- `docker compose up` levanta el entorno completo: `postgres` + `garage` + `backend` + `frontend`.
- OAuth en local: segunda GitHub OAuth App ("ctx-dev") con callback a `localhost`.
- Emails en dev: a consola. LLM: APIs reales, modelo barato vía variable de entorno.
- **Seed de datos**: script con 2-3 usuarios, ~5 artículos reales migrados y 1 libro pequeño procesado.
- Migraciones de schema con **Alembic** desde el día 1.

## Infraestructura

La gestión de infraestructura (backups de Postgres y Garage, Cloudflare delante de los subdominios, red y exposición del servidor) no es cuestión de este proyecto; es una responsabilidad aparte.

## Migración

- Artículos ya publicados: script de migración markdown → Postgres.
- Issues de GitHub: eliminadas (2026-07-01); las propuestas nuevas nacen en la web.
- GitHub Pages y workflows de publicación: se apagan al completar la fase 3.

## Fases

1. **Backend base** — FastAPI + Postgres + OAuth GitHub + users pending/active + `/admin` mínimo + deploy en Coolify.
2. **Artículos** — migración a BBDD, web con login, quiz/leídos por usuario.
3. **Propuestas y pipeline** — propuestas + votos en web; triaje/writer/reviewer movidos al backend; se apagan issues/Actions/Pages.
4. **Libros** — upload PDF, troceado + validación admin, generación de píldoras, plan, progreso.
5. **Landing + Resend** — home pública de producto, notificaciones al admin.

## Pendiente

- Nombre definitivo de subdominios y dominio final del producto.
- Diseño de la landing (incluye decidir si "Cómo funciona" se integra en ella).
- Notificaciones a usuarios (futuro; v1 solo admin).
