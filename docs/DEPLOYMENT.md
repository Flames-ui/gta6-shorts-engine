# Deployment

## What must exist before the first deploy

| Requirement | Why |
| --- | --- |
| PostgreSQL 16 or 17 database (dedicated to this backend; tested on 17) | Jobs, sources, clips and events |
| Redis (`redis://` or `rediss://`) | Queue, worker locks and heartbeats |
| `AUTH_JWT_SECRET` (32+ characters) | Verifies every client token |
| `GEMINI_API_KEY` | Highlight analysis (production) |
| Storage: Supabase Storage bucket `studio-private` (multi-host), or a local volume (single host) | Source files and rendered outputs |
| FFmpeg and FFprobe in the image | Probing, rendering, caption burn-in (the Dockerfile installs them) |

Generate the JWT secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and store it in your platform's secret manager. Don't reuse it across environments.

## Render (recommended)

`render.yaml` defines the API, the worker, Key Value (Redis) and a PostgreSQL database.

1. **Create the blueprint.** In Render, choose *New → Blueprint* and select this repository and branch.
2. **Fill the secrets** marked `sync: false`: `AUTH_JWT_SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, and `ADMIN_USER_IDS` or `ADMIN_API_KEY`. Set `CORS_ORIGINS` to your web app's origin(s).
3. **Create the Supabase bucket** `studio-private` with **public access off**. Render workers and the API both need it, so local storage won't work here.
4. **Deploy.** The API runs `python -m app.migrate` as its pre-deploy command, so migrations apply before new code serves traffic. A failed migration stops the release.
5. **Verify.** `GET https://<api>/health` returns 200. `GET https://<api>/ready` returns 200 only when the database, Redis, storage and FFmpeg are all usable.

Scaling: keep the API at one or more instances. Workers scale horizontally; each job takes a lock, so two workers never process the same job. Start with `MAX_PARALLEL_RENDERS=2` per worker and raise it only with enough CPU.

## Docker (any host)

```bash
docker build -t studio-backend .
docker run --env-file .env -p 8000:8000 studio-backend                 # API
docker run --env-file .env studio-backend python -m app.worker         # worker (one or more)
docker run --env-file .env studio-backend python -m app.migrate        # run before starting new code
```

`docker-compose.yml` runs all of this locally with PostgreSQL and Redis.

## Running without Docker

Requires Python 3.13, FFmpeg/FFprobe on `PATH` (with libass for captions), and reachable PostgreSQL and Redis.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.migrate
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker        # in a second process
```

## Migrations

- Apply with `python -m app.migrate`. It checks only the database settings, so a release job doesn't need provider keys.
- `python -m app.migrate --status` lists the known migration versions.
- Migrations are forward-only in production. `migrations/001_studio_schema.down.sql` exists for disposable development databases only; never run it against data you care about.

## Rollback

- **Code:** redeploy the previous image or commit. The schema is forward-compatible for one release, so the previous code can run against the newer schema.
- **Jobs in flight:** cancel them through `POST /jobs/{id}/cancel`, or let stale-heartbeat recovery requeue them after the worker stops.

## Verifying a deployment

1. `GET /health` and `GET /ready` return 200.
2. Upload a short test video you own and create a job with `clip_count: 5`.
3. Poll `GET /jobs/{id}` until `completed` or `failed`. A `failed` job must show an `error_code`.
4. For a completed job, download one clip and check it is 1080×1920 and plays.
5. Check `GET /admin/diagnostics` shows the worker heartbeat and an empty or shrinking queue.

## Operational notes

- **Whisper models** download on first use. Mount a persistent cache (`HF_HOME=/var/cache/huggingface`) so restarts don't re-download.
- **Temporary files** live under `TEMP_DIR` and are removed after each job. Keep it on local disk with enough space for a 30-minute source plus renders.
- **Retention:** `SOURCE_RETENTION_DAYS` and `TEMP_RETENTION_HOURS` control cleanup.
- **YouTube retrieval** stays off unless you have rights to the sources you process. Turn it on only deliberately.
