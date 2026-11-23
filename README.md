# AI Video Editing Studio: Backend

A general-purpose backend for turning one long video into **exactly five distinct vertical short clips** (1080×1920) with synchronized captions.

You give it a YouTube link you have rights to, or upload a file. It checks the media, transcribes the speech, has an AI model pick the strongest moments, renders five clips with FFmpeg, validates each output, and stores them in private storage. Clients poll the job, then download the MP4 and caption files.

> **Status:** the code and its automated tests are complete (236 passing, including real FFmpeg rendering and real PostgreSQL). Production deployment has **not** been verified. See [Known limits](#known-limits).

## What it does

- **Sources:** register a YouTube URL (metadata only, unless retrieval is enabled for rights-cleared sources) or upload MP4, MOV, MKV or WebM.
- **Jobs:** each job produces five clips or fails with a specific, explicit reason. A job is never marked complete until all five clips are stored and validated.
- **Highlights:** Whisper (local) transcribes; Gemini (text only, timestamps included) proposes moments. Only moments scoring at least 75 and fitting 15–90 seconds are kept, and no two clips overlap.
- **Captions:** SRT, WebVTT and ASS files per clip, burned into the video with one of five presets.
- **Operations:** Redis/RQ workers with locks, heartbeats and stale-job recovery; bounded retries; structured JSON logs; `/health`, `/ready` and an admin diagnostics view.
- **Security:** bearer-token auth scoped to each owner, private storage, signed short-lived download links, upload and URL validation, and no provider keys in responses or logs.

## Quick start (local, Docker)

```bash
cp .env.example .env
# Set AUTH_JWT_SECRET (32+ characters) and GEMINI_API_KEY in .env
docker compose up --build
```

This starts PostgreSQL, Redis, a one-shot migration, the API on `http://localhost:8000` and one worker. Interactive API docs are at `http://localhost:8000/docs`.

Without Docker, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#running-without-docker).

## A complete flow

```bash
TOKEN=...   # a JWT signed with AUTH_JWT_SECRET, audience "authenticated", subject = owner id
API=http://localhost:8000/api/v1

# 1. Upload a video you have the rights to process
curl -H "Authorization: Bearer $TOKEN" -F "file=@talk.mp4" $API/sources/upload

# 2. Start a job for the source (exactly five clips)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source_id":"<source id>","clip_count":5,"caption_preset":"bold_creator"}' $API/jobs

# 3. Poll until status is completed or failed
curl -H "Authorization: Bearer $TOKEN" $API/jobs/<job id>

# 4. Get a short-lived download link for a clip, and its captions
curl -H "Authorization: Bearer $TOKEN" $API/clips/<clip id>/download-url
curl -H "Authorization: Bearer $TOKEN" $API/clips/<clip id>/captions/srt
```

The full reference is in [docs/API.md](docs/API.md).

## Documentation

| Doc | For |
| --- | --- |
| [docs/API.md](docs/API.md) | Every endpoint, request and response shapes, errors, rate limits |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, the pipeline, job states, data model, security model |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Configuration, Render blueprint, Docker, migrations, rollback |
| [docs/TESTING.md](docs/TESTING.md) | Test suites, how to run them, what is and is not covered |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Failure codes and what to do about each |

## Repository layout

```
app/
  main.py, config.py, auth.py, db.py, storage.py, queue.py, worker.py, migrate.py
  api/            FastAPI routers (sources, jobs, clips, files, usage, admin)
  pipeline/       orchestrator (job state machine) and highlight selection
  media/          upload checks, YouTube URL and retrieval, FFprobe, FFmpeg, rendering
  ai/             highlight-analysis provider interface, Gemini adapter, analysis
  captions/       transcript timing and SRT/VTT/ASS formats
  transcription.py   faster-whisper wrapper
migrations/       forward-only SQL schema (+ a down file for development only)
tests/            unit, database, API, worker and end-to-end suites
```

## Known limits

- **YouTube retrieval is off by default.** Turning it on (`YOUTUBE_RETRIEVAL_ENABLED=true`) is for sources you have the rights to process. The backend never bypasses DRM, authentication, CAPTCHAs or platform restrictions, and it doesn't read browser cookies. Videos that refuse retrieval fail with a clear code.
- **Highlight quality depends on the AI provider.** The Gemini path has been tested with mocked responses; a live run against your key is still required.
- **Whisper models download on first use** (several hundred MB for `base`). Plan for that in worker start-up time and storage.
- **Local file storage works on one host only.** Use Supabase Storage (or another shared store) for any multi-host deployment.
