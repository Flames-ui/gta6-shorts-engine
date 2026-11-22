# Multi-Category Viral Content Engine & Shorts Production Backend

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python)](https://python.org)
[![Supabase](https://img.shields.io/badge/Supabase-Database%20%26%20Storage-3ECF8E?style=flat-square&logo=supabase)](https://supabase.com)
[![Redis RQ](https://img.shields.io/badge/Queue-Redis%20RQ-DC382D?style=flat-square&logo=redis)](https://python-rq.org)
[![Buffer](https://img.shields.io/badge/Publishing-Buffer%20OAuth%202.0-000000?style=flat-square)](https://buffer.com)
[![Render](https://img.shields.io/badge/Deploy-Render%20Blueprint-46E3B7?style=flat-square&logo=render)](https://render.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A scalable, secure, production-grade backend engine engineered for multi-category viral short-form video discovery, AI-assisted highlight generation, vertical 9:16 video production, and social publishing via Buffer (TikTok, YouTube Shorts, Instagram Reels). Fully integrated with Supabase database & storage, Redis/RQ background worker architecture, and designed to connect with Lovable web dashboards.

---

## Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Multi-Category Content Engine](#2-multi-category-content-engine)
3. [Content Discovery & Rights Management](#3-content-discovery--rights-management)
4. [Security & Isolation Model](#4-security--isolation-model)
5. [Buffer Social Publishing Integration](#5-buffer-social-publishing-integration)
6. [Analytics & Scheduling Engine](#6-analytics--scheduling-engine)
7. [Local Setup & Development](#7-local-setup--development)
8. [Environment Variables Reference](#8-environment-variables-reference)
9. [Supabase Setup & SQL Migrations](#9-supabase-setup--sql-migrations)
10. [Render Cloud Deployment](#10-render-cloud-deployment)
11. [Lovable Dashboard Integration & API Contracts](#11-lovable-dashboard-integration--api-contracts)
12. [Troubleshooting & Verification](#12-troubleshooting--verification)

---

## 1. Architecture Overview

```
                      ┌───────────────────────────────────────────────┐
                      │            Lovable Frontend App               │
                      │  (Multi-Category Dashboard, Queue, Approval)  │
                      └───────────────────────┬───────────────────────┘
                                              │ REST API (HTTPS + X-API-Key / Supabase JWT)
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                Render Deployment Cloud                                  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │   gta6-shorts-engine (Single-Instance Combined Web + Worker Service)              │  │
│  │   Supervised by app/start_combined.sh:                                            │  │
│  │   ├─ Process 1: FastAPI HTTP Server (Uvicorn on 0.0.0.0:8000)                     │  │
│  │   │  - REST API & Health Probes (/health, /health/ready, /health/live)            │  │
│  │   │  - Pydantic v2 Validation, SSRF Defense, JWT & Tenant Isolation               │  │
│  │   └─ Process 2: Redis RQ Worker (python -m app.worker on queue: viral-shorts)    │  │
│  │      - FFmpeg Video Processing & 9:16 Vertical Cropping                           │  │
│  │      - Local Whisper Audio Transcription & Gemini 2.5 Flash Highlights            │  │
│  │      - Orphaned Job Recovery upon restart                                         │  │
│  │   * NOTE: Video rendering, Whisper AI, and API traffic share CPU & RAM.           │  │
│  └───────────────┬───────────────────────────────────────────────────┬───────────────┘  │
│                  │ Enqueue Jobs                                      │ Dequeue & Run    │
│                  ▼                                                   ▼                  │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │   gta6-shorts-redis (Private KeyValue Redis)                                      │  │
│  │   - Redis RQ queue ('viral-shorts'), job deduplication & distributed locks        │  │
│  │   - ipAllowList: [] (Private internal access only)                                │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                            External Services & Storage                                  │
│                                                                                         │
│  ┌───────────────────────────────┐                  ┌────────────────────────────────┐  │
│  │   Supabase Cloud Platform     │                  │   Buffer Publishing API        │  │
│  │   - PostgreSQL + RLS Security │                  │   - TikTok Direct Publishing   │  │
│  │   - Private S3 Video Storage  │                  │   - YouTube Shorts Channels    │  │
│  │   - Signed CDN Download URLs  │                  │   - Multi-Channel Scheduling   │  │
│  └───────────────────────────────┘                  └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Multi-Category Content Engine

The engine is completely decoupled from GTA 6-only logic and supports custom and pre-configured viral entertainment categories:

| Category ID | Display Name | Core Content Profile |
| :--- | :--- | :--- |
| `gaming` | Gaming & Gaming Discoveries | GTA 6 trailers/gameplay, competitive clutches, unexpected gaming physics |
| `viral_entertainment` | Viral Entertainment | Unexpected real-life moments, reactions, comedy hooks, human interest |
| `animals_nature` | Animals & Wildlife | Cute moments, heroic rescues, rare wildlife interactions, nature spectacles |
| `tech_ai` | Technology & Futuristic AI | Emerging AI breakthroughs, robotics demos, gadgets, future tech |
| `sports` | Sports & Athletic Feats | Unbelievable goals, clutch buzzer-beaters, extreme stunts, athletic drama |

Categories can be dynamically queried or created at `/api/v1/categories` without touching core video rendering code.

---

## 3. Content Discovery & Rights Management

Every content item processed through the system must be verified against legal rights compliance gates:

- **Pending Verification**: Discovered content cannot enter video rendering or background queues until rights are recorded.
- **Authorized for Editing**: Rights holder has provided permission to create short-form transformative derivative content.
- **Authorized Platforms**: Restricted licensing allowing publication only on approved channels (e.g., TikTok only).
- **Rejected**: Banned from ingestion and production.

Content deduplication is enforced by tracking source hashes, canonical URLs, and channel identifiers.

---

## 4. Security & Isolation Model

1. **Production Fail-Closed Enforcement**: If `ENGINE_API_KEY` or `SUPABASE_SERVICE_ROLE_KEY` is missing in `ENVIRONMENT=production`, the application immediately halts and refuses requests rather than allowing unauthenticated access.
2. **SSRF & Private Network Defense**:
   - Rejects loopback (`127.0.0.1`), private subnets (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), and AWS/GCP link-local metadata addresses (`169.254.169.254`).
   - Disallows non-HTTP/HTTPS schemes (`file://`, `ftp://`).
   - Strict redirect re-validation.
3. **CORS Hardening**: Strict origin matching based on `ALLOWED_CORS_ORIGINS` (including Lovable preview domains); wildcards (`*`) with credentials enabled are strictly prohibited.
4. **Row Level Security (RLS)**: Public anonymous access to database tables (`processing_jobs`, `generated_clips`, `content_sources`) is revoked. Service role holds backend administrative rights while authenticated users can only view their own records.
5. **Private Media Storage**: Video clips and high-resolution assets are stored in private Supabase buckets, accessed exclusively through expiring signed URLs.

---

## 5. Buffer Social Publishing Integration

Buffer serves as the official social distribution layer:
- **Authentication**: Connect via OAuth 2.0 (`/api/v1/publishing/buffer/connect`).
- **Channel Discovery**: Fetch linked TikTok and YouTube Shorts channels (`/api/v1/publishing/channels`).
- **Scheduling**: Post clips with platform-tailored hooks, descriptions, and hashtags (`/api/v1/clips/{clip_id}/schedule`).
- **Manual Gate**: Clips remain in `awaiting_approval` status until explicitly approved by human reviewers.

---

## 6. Analytics & Scheduling Engine

- **Automated Production Targets**: Set daily production goals (default: 15–20 videos per day) with category-weighted quotas and active publishing windows (e.g., 11:00–14:00 and 17:00–22:00 UTC).
- **Metric Verification**: Tracks `views`, `likes`, `comments`, `shares`, `reach`, and `completion_rate`. Missing or un-synced metrics are marked as `unavailable` or `pending_sync` rather than fabricated with synthetic numbers.
- **Feedback Loops**: Historical clip performance identifies top-performing categories and formats to guide future automated batching.

---

## 7. Local Setup & Development

### Prerequisites
- Python 3.11+
- FFmpeg 6.0+ (`ffmpeg`, `ffprobe`) installed on your system PATH
- Redis server (or Docker Redis)

### Installation
```bash
# Clone the repository
git clone https://github.com/Flames-ui/gta6-shorts-engine.git
cd gta6-shorts-engine

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
```

### Running Tests
```bash
PYTHONPATH=. pytest tests/ -v
```

### Starting the API Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Starting the Background Worker
```bash
python -m app.worker
```

---

## 8. Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | Yes | `development` | Set to `production` for Render deployment |
| `ENGINE_API_KEY` | Yes (Prod) | - | Shared secret for backend API authentication |
| `GEMINI_API_KEY` | Recommended | - | Google Gemini AI key for highlight detection |
| `SUPABASE_URL` | Yes (Prod) | - | Supabase Project URL (`https://xyz.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes (Prod) | - | Supabase backend service role key |
| `SUPABASE_STORAGE_BUCKET` | No | `generated_clips` | Bucket name for persistent clip storage |
| `REDIS_URL` | Yes (Prod) | `redis://localhost:6379/0`| Connection URL for Redis/RQ queue |
| `BUFFER_CLIENT_ID` | For Buffer | - | Buffer OAuth App Client ID |
| `BUFFER_CLIENT_SECRET` | For Buffer | - | Buffer OAuth App Client Secret |
| `BUFFER_REDIRECT_URI` | For Buffer | - | Callback URL for Buffer OAuth |
| `ALLOWED_CORS_ORIGINS` | No | Lovable domains | Comma-separated list of approved frontend origins |

---

## 9. Supabase Setup & SQL Migrations

Run the SQL migration in `supabase/migrations/20261121010000_multi_category_and_security.sql` in your Supabase SQL Editor.

Key tables provisioned with Row Level Security:
- `categories`: Multi-category taxonomy, AI prompts, and scoring weights.
- `content_sources` & `source_permissions`: Discovery sources and copyright authorization gates.
- `discovered_content`: Discovered content feed awaiting processing.
- `processing_jobs` & `processing_logs`: Real-time job status and execution telemetry.
- `generated_clips` & `clip_metadata`: 9:16 vertical video clips, signed URLs, and hooks.
- `publishing_accounts`, `connected_channels`, `publishing_queue`: Buffer integration records.
- `platform_analytics` & `production_schedules`: Engagement tracking and daily quota rules.

---

## 10. Render Cloud Deployment

The repository includes a verified, cost-optimized `render.yaml` Blueprint configuring a **single-instance combined web and worker service** alongside a private Key-Value Redis database, completely avoiding the cost of a separate background worker service.

> **CRITICAL RESOURCE NOTICE**:
> This is a **single-instance, resource-constrained deployment**. The FastAPI web server and the Redis RQ background worker run inside the **same container**, sharing the exact same CPU cores and RAM allocations. Video rendering with FFmpeg, Whisper audio transcription, and incoming HTTP API traffic execute on this shared instance.
> - **Do not claim production-scale capacity without empirical load testing.**
> - `MAX_CONCURRENT_JOBS` is strictly constrained to `2` (or `1` on lower resource tiers) to prevent Out-Of-Memory (OOM) process termination during heavy FFmpeg filtering and subtitle burning.
> - `PUBLISHING_AUTOMATION_ENABLED` defaults to `false` to ensure safe staged rollout without inadvertent public postings.

---

### 1. Service Topology
- **`gta6-shorts-engine` (Combined Web & Worker Service)**:
  - **Runtime**: Production Docker container (`Dockerfile`).
  - **Supervisor**: `app/start_combined.sh` initiates and supervises two concurrent child processes:
    1. `python -m app.worker`: Background worker listening on queue `viral-shorts`, performing orphaned job recovery on startup and handling video rendering.
    2. `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`: REST API server handling web requests.
  - **Supervision & Signal Handling**: The supervisor traps `SIGTERM` and `SIGINT`, forwards termination signals to both child processes for graceful shutdown, automatically terminates the surviving process if either child crashes, and propagates nonzero exit status back to Render for automatic restart.
  - **Instance Plan**: `standard` (or higher) to provide adequate CPU and RAM for simultaneous HTTP traffic and FFmpeg video transcoding.
  - **Health Check**: Configured on `/health` (HTTP 200 with dependency statuses).
  - **Readiness Probes**: `/health/ready` (responds 503 if Supabase or Redis are unavailable in production).
  - **Liveness Probes**: `/health/live` (responds 200 if server process is running).

- **`gta6-shorts-redis` (Private Redis / Key-Value)**:
  - **Type**: `keyvalue` (Private Redis instance).
  - **Access Control**: `ipAllowList: []` — completely private and unreachable from the public internet. Accessible only by internal Render services.
  - **Connection**: Web service connects automatically via Render's `property: connectionString`.

---

### 2. Complete Environment Variable Configuration Matrix

The table below details every environment variable configured in `render.yaml`, distinguishing values that Render or Blueprint manages automatically from secrets that **you must supply**:

| Environment Variable | Target Service | Category / Origin | Description |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | `gta6-shorts-engine` | Built-in | Set to `production` for strict fail-closed security. |
| `FAIL_CLOSED_IN_PRODUCTION` | `gta6-shorts-engine` | Built-in | Set to `true` to reject unverified JWTs and enforce Supabase/Redis. |
| `LOG_LEVEL` | `gta6-shorts-engine` | Built-in | Logging verbosity (`INFO`). |
| `PORT` | `gta6-shorts-engine` | Built-in | Default web port (`8000`). |
| `CORS_ORIGINS` | `gta6-shorts-engine` | Built-in | Allowed frontend origins (`https://lovable.dev,https://lovable.app`). |
| `PUBLISHING_AUTOMATION_ENABLED` | `gta6-shorts-engine` | Safety Default | Default `false` during deployment testing to avoid accidental public posts. Set `true` only when ready to enable autonomous publishing. |
| `ENGINE_API_KEY` | `gta6-shorts-engine` | **Render Auto-Generated** | 32-character cryptographically random server secret. |
| `ENGINE_API_KEY_PREVIOUS` | `gta6-shorts-engine` | Optional Secret | Kept for zero-downtime key rotation. |
| `TOKEN_ENCRYPTION_KEY` | `gta6-shorts-engine` | **Render Auto-Generated** | Encryption key used for AES token encryption at rest. |
| `ADMIN_USER_IDS` | `gta6-shorts-engine` | **User-Supplied** (Optional) | Comma-separated list of Supabase `user_id`s granted administrator privileges. |
| `ADMIN_EMAILS` | `gta6-shorts-engine` | **User-Supplied** (Optional) | Comma-separated list of admin email addresses. |
| `SUPABASE_URL` | `gta6-shorts-engine` | **User-Supplied (Mandatory)** | Your Supabase project URL (`https://<project-ref>.supabase.co`). |
| `SUPABASE_SERVICE_ROLE_KEY` | `gta6-shorts-engine` | **User-Supplied (Mandatory)** | Your secret `service_role` key. Kept exclusively on the server, never exposed. |
| `SUPABASE_JWT_SECRET` | `gta6-shorts-engine` | **User-Supplied (Mandatory)** | Supabase Auth JWT secret used to cryptographically verify user tokens. |
| `SUPABASE_STORAGE_BUCKET` | `gta6-shorts-engine` | Built-in | Name of private storage bucket (`shorts`). |
| `REDIS_URL` | `gta6-shorts-engine` | **Render Key-Value (Sync)** | Private Redis connection string wired directly from `gta6-shorts-redis`. |
| `QUEUE_NAME` | `gta6-shorts-engine` | Built-in | RQ queue name (`viral-shorts`). |
| `MAX_CONCURRENT_JOBS` | `gta6-shorts-engine` | Built-in | Concurrency ceiling (`2`) to limit resource contention. |
| `JOB_TIMEOUT_SECONDS` | `gta6-shorts-engine` | Built-in | Maximum execution timeout per video job (`1800` seconds / 30 min). |
| `LLM_PROVIDER` | `gta6-shorts-engine` | Built-in | AI highlight provider (`gemini`). |
| `GEMINI_API_KEY` | `gta6-shorts-engine` | **User-Supplied (Mandatory)** | Google Gemini API key for intelligent video highlight detection. |
| `GEMINI_MODEL` | `gta6-shorts-engine` | Built-in | Model version (`gemini-2.5-flash`). |
| `OPENAI_API_KEY` | `gta6-shorts-engine` | **User-Supplied** (Optional) | OpenAI API key for secondary fallback. |
| `OPENAI_MODEL` | `gta6-shorts-engine` | Built-in | OpenAI model (`gpt-4o-mini`). |
| `BUFFER_CLIENT_ID` | `gta6-shorts-engine` | **User-Supplied** (For Buffer) | Buffer OAuth application Client ID. |
| `BUFFER_CLIENT_SECRET` | `gta6-shorts-engine` | **User-Supplied** (For Buffer) | Buffer OAuth application Client Secret. |
| `BUFFER_REDIRECT_URI` | `gta6-shorts-engine` | **User-Supplied** (For Buffer) | `https://<YOUR_RENDER_URL>/api/v1/publishing/buffer/callback`. |
| `YOUTUBE_CLIENT_ID` | `gta6-shorts-engine` | **User-Supplied** (For YouTube) | Google Cloud OAuth Client ID for direct YouTube Shorts publishing. |
| `YOUTUBE_CLIENT_SECRET` | `gta6-shorts-engine` | **User-Supplied** (For YouTube) | Google Cloud OAuth Client Secret. |
| `YOUTUBE_REDIRECT_URI` | `gta6-shorts-engine` | **User-Supplied** (For YouTube) | Google OAuth callback URI. |
| `LOCAL_WHISPER_MODEL` | `gta6-shorts-engine` | Built-in | Local Whisper model size (`base`). |
| `LOCAL_WHISPER_DEVICE` | `gta6-shorts-engine` | Built-in | Whisper compute device (`cpu`). |
| `OUTPUT_DIR` / `LOCAL_OUTPUT_DIR` | `gta6-shorts-engine` | Built-in | Local output path (`/app/output`). |
| `TEMP_DIR` | `gta6-shorts-engine` | Built-in | Scratch directory (`/app/tmp`). |
| `DAILY_TARGET_MIN` / `MAX` | `gta6-shorts-engine` | Built-in | Autonomous daily clip generation quota (`15` to `20`). |

---

### 3. Deploying the Blueprint on Render
1. Navigate to the **Render Dashboard** > **Blueprints** > **New Blueprint Instance**.
2. Connect the repository `Flames-ui/gta6-shorts-engine` and select the `main` branch.
3. Render will parse `render.yaml` and prompt you to input the **User-Supplied Mandatory Values**:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_JWT_SECRET`
   - `GEMINI_API_KEY`
   - Buffer / YouTube credentials (if social publishing is being activated)
4. Click **Apply**. Render will:
   - Provision the private `gta6-shorts-redis` Key-Value database.
   - Build and launch the `gta6-shorts-engine` Docker image.
   - Execute `CMD ["bash", "app/start_combined.sh"]` to start both the worker and FastAPI server.
5. Once deployment completes, check the health endpoints:
   ```bash
   curl -I https://<YOUR_RENDER_APP_URL>/health
   curl -I https://<YOUR_RENDER_APP_URL>/health/ready
   ```

## 11. Lovable Dashboard Integration & API Contracts

Lovable dashboards connect to the backend using standard HTTPS requests with the `X-API-Key: <ENGINE_API_KEY>` header:

### Key Endpoints for Lovable:
- `GET /health`, `/health/ready`, `/health/live`: Health, readiness, and liveness probes.
- `GET /api/v1/auth/me`: Inspect caller identity, role, and administrator status.
- `GET /api/v1/dashboard/stats`: Aggregated metrics for Lovable summary cards (total jobs, active jobs, approved clips, active categories, workers).
- `GET /api/v1/categories`: Retrieve all active and custom categories.
- `PATCH /api/v1/categories/{category_id}`: Enable, disable, or tune category prompts and scoring weights (Admin only).
- `POST /api/v1/jobs`: Create video processing job (`url`, `category_id`, `num_clips`, `aspect_ratio`, `mode`).
- `GET /api/v1/jobs/{job_id}`: Poll job progress, current stage, and execution logs.
- `GET /api/v1/clips`: List clips with pagination and category filtering (scoped to tenant).
- `GET /api/v1/clips/{clip_id}`: Retrieve clip details, storage URL, and thumbnail.
- `POST /api/v1/clips/{clip_id}/approve`: Review and approve a clip for publication.
- `POST /api/v1/clips/{clip_id}/schedule`: Submit approved clip to Buffer queue with quota and duplicate checks.
- `GET /api/v1/publishing/buffer/connect`: Generate single-use Buffer OAuth authorization URL.
- `POST /api/v1/publishing/buffer/callback`: Complete Buffer OAuth with encrypted token persistence.
- `GET /api/v1/publishing/channels`: List connected social channel profiles for authenticated user.
- `POST /api/v1/publishing/sync`: Synchronize publishing queue status with Buffer provider API.
- `GET /api/v1/discovery/feed`: Retrieve discovered content opportunities.
- `POST /api/v1/discovery/poll`: Actively poll sources with URL normalization and deduplication.
- `GET /api/v1/quotas`: Check daily production target limits and real-time category distribution.
- `GET /api/v1/analytics/overview`: Aggregate views, completion rate, and platform metrics.
- `GET /api/v1/analytics/recommendations`: Empirical data-driven production recommendations.
- `GET /api/v1/audit-logs`: Review security and operational audit logs.

---

## 12. Troubleshooting & Verification

- **Redis Offline in Development**: In development or test mode, the system safely falls back to local in-process execution for developer convenience. In production, it fails closed to protect workload integrity.
- **Missing AI Key**: If Gemini/OpenAI API keys are not supplied, the pipeline activates heuristic highlight fallback mode to allow end-to-end rendering test execution without crashing.
- **Clip Validation Failures**: Videos failing minimum length, audio presence, or resolution checks are logged and quarantined rather than published.

---

## 13. Production Readiness & External Credentials Checklist

The engine is engineered to run in a fully verified, fail-closed posture in production (`ENVIRONMENT=production` and `FAIL_CLOSED_IN_PRODUCTION=true`). Below is an explicit operational status matrix:

| Component | Implemented Functionality | External Credential Requirement |
| :--- | :--- | :--- |
| **Authentication & RBAC** | Cryptographic Supabase JWT (HS256) signature verification, claim & expiry validation, constant-time API key comparisons, strict tenant isolation & non-admin fail-closed checks. | `SUPABASE_JWT_SECRET` and `ENGINE_API_KEY`. |
| **Database & Persistence** | Supabase PostgreSQL client with full CRUD for jobs, clips, sources, permissions, discovered content, and OAuth states. Row Level Security policies enabled with `security_invoker = true` on compatibility views. | `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (mandatory in production; SQLite permitted only in local/test). |
| **Queue & Background Worker** | Redis RQ task queue with retry backoff, connection state checks, sanitized logging, and zero-worker reporting when offline. | `REDIS_URL` (mandatory in production; thread-fallback blocked). |
| **Content Discovery & Rights** | Source registration, database-persisted rights permissions, pre-production and pre-publishing copyright gates. | Public RSS feeds operate immediately; partner feeds require platform/API approval. |
| **AI Highlight Analysis** | Gemini 2.5 Flash and OpenAI GPT-4o-mini client integration with structured schema parsing. | `GEMINI_API_KEY` or `OPENAI_API_KEY` (heuristic fallback active in dev/test when absent). |
| **Video Production Engine** | Video downloading, Whisper audio transcription, dynamic vertical 9:16 cropping, visual hook overlay, and duration validation. | FFmpeg 6+ / 7+ system binary and local/container compute. |
| **Social Publishing (Buffer)** | Cryptographically random OAuth state generation, single-use state validation, external callback handling without API keys, platform-specific constraints validation, and scheduling queue. | Requires `BUFFER_CLIENT_ID`, `BUFFER_CLIENT_SECRET`, and `BUFFER_REDIRECT_URI` registered in Buffer Developer Portal. |
| **Social Publishing (Direct YouTube)** | OAuth 2.0 direct publishing fallback. | Requires `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` with YouTube Data API v3 scope approval from Google Cloud Console. |
| **Health & Observability** | `/health` (general status), `/health/ready` (Kubernetes/Render readiness probe, returns 503 if Supabase or Redis are unavailable in production), `/health/live` (liveness probe). | None. |

---

## 14. Step-by-Step Supabase Migration Instructions

1. Log into your [Supabase Dashboard](https://supabase.com/dashboard).
2. Open your project and navigate to the **SQL Editor**.
3. Copy and execute the contents of `supabase/migrations/20261121010000_multi_category_and_security.sql`.
4. Ensure the `shorts` storage bucket exists under **Storage** > **Buckets**, or create a bucket named `shorts` with appropriate read permissions for signed clip streaming.
5. In **Project Settings** > **API**, retrieve your:
   - Project URL (`SUPABASE_URL`)
   - `service_role` secret key (`SUPABASE_SERVICE_ROLE_KEY`)
   - JWT Secret (`SUPABASE_JWT_SECRET`)
6. Populate these values in your Render environment variables or `.env`.

---

## 15. Step-by-Step Buffer Connection Instructions

1. Register an application in the [Buffer Developer Portal](https://buffer.com/developers/api).
2. Set your **Redirect URI** to:
   `https://<YOUR_RENDER_SERVICE_URL>/api/v1/publishing/buffer/callback`
3. Copy your `Client ID` and `Client Secret` into `BUFFER_CLIENT_ID` and `BUFFER_CLIENT_SECRET`.
4. From your Lovable frontend or API client:
   - Call `GET /api/v1/publishing/buffer/connect`.
   - The endpoint generates a cryptographically random, persistent OAuth state and returns the Buffer authorization URL.
   - Redirect your browser to the authorization URL.
   - Buffer redirects back to `/api/v1/publishing/buffer/callback?code=...&state=...`.
   - The engine validates and consumes the OAuth state (single-use), exchanges the authorization code for an access token, retrieves connected channels/profiles, and saves them in `publishing_accounts` and `connected_channels`.


## Supabase production setup and recovery

1. Rotate any service-role key that has been pasted into chat, logs, tickets, or source control. Store the replacement only in the deployment platform's secret manager as `SUPABASE_SERVICE_ROLE_KEY`; never use it in browser code or commit it.
2. Apply `supabase/migrations/20261121000000_initial_schema.sql`, then the schema/security migrations in timestamp order, including `20261122000000_persistent_production_state.sql`, using the Supabase CLI or SQL Editor as a database owner. The latest migration adds durable discovery/reconciliation events, render/job recovery metadata, scheduling state, owner-scoped RLS, and private `shorts` storage policies.
3. Set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `TOKEN_ENCRYPTION_KEY`, and `REDIS_URL` as server-side deployment secrets. The API and worker use the service-role client only server-side; clients authenticate with Supabase user JWTs.
4. `shorts` is private. Objects use `{user_id}/{job_id}/{filename}` keys and the API returns expiring signed URLs. Never mark this bucket public for convenience.
5. Processing and publication state is stored in Supabase; Redis remains the execution queue. On worker restart, stale processing jobs can be recovered from persisted status/heartbeat and retried without treating unaccepted provider requests as published.

Do not place a service-role key in this README, `.env.example`, frontend variables, or Git history. SQL migrations must be applied with database-owner access; a Supabase REST service-role token does not provide a general SQL execution endpoint.

---

## 13. Milestone 1 — Campaign-Aware Gaming Clipping Engine

The system has been enhanced with a private, single-operator campaign clipping engine focused exclusively on **GTA 6** and **gaming content** for **TikTok** and **YouTube Shorts** campaigns.

### Key Capabilities

1. **Campaign Data Model & Rule Reader (`app/services/campaign_rule_reader.py`)**
   - Natural language and structured campaign analysis extracting reward models (`cpm`, `prize_pool`, `fixed_per_clip`), eligible games, accepted platforms (`tiktok`, `youtube_shorts`), duration constraints, required hashtags (`#GTA6`, `#KaiCenat`), required mentions (`@kaicenat`), permitted footage, and disqualification triggers.
   - **Untrusted Input Protection**: Defense-in-depth sanitization detecting prompt injection and control sequence attempts in raw campaign texts, flagging security warnings without code execution.
   - **Ambiguity Detection**: Missing bounds or vague payout formulas immediately flag `needs_review` rather than inventing assumptions.

2. **Strict Campaign Eligibility Gate (`app/services/campaign_eligibility.py`)**
   - **"No campaign, no clipping. No verified earning opportunity, no production."**
   - Direct requests to `/api/v1/jobs` without an active, approved campaign and verified source footage are rejected (HTTP 400).
   - Validates campaign active status, TikTok/YouTube Shorts support, non-expired deadline, gaming category qualification, and source footage rights.

3. **Marketplace Connector Architecture (`app/services/marketplaces/`)**
   - Extensible marketplace adapter interface (`BaseMarketplaceAdapter`) supporting discovery, instruction extraction, and session verification.
   - **Content Rewards Adapter (`content_rewards.py`)**: Supports discovery of active gaming campaigns, detects login/CAPTCHA/session requirements, and provides a compliant operator manual import workflow (`POST /api/v1/campaigns/import`) avoiding unauthorized scraping.

4. **Source-Video Authorization (`app/services/source_authorization.py`)**
   - Stores source URL, original creator, permission evidence, allowed derivative uses, authorized platforms, expiry date, and verification status (`verified`, `pending_verification`, `rejected`).
   - Prevents unauthorized footage from entering rendering queues.

5. **Integrated Gaming Pipeline & Moment Detection (`app/services/gameplay_analyzer.py`)**
   - Detects 9 core gaming moment types:
     - `unexpected_gameplay_events`
     - `funny_failures`
     - `close_escapes`
     - `impressive_skills`
     - `unexpected_discoveries`
     - `dramatic_moments`
     - `rare_interactions`
     - `strong_reactions`
     - `memorable_gta6_moments`
   - Evaluates audio energy peaks, visual motion variance, and gaming keyword indicators, ranking multiple candidates before vertical 9:16 rendering.

6. **Compliance & Quality Validation (`app/services/compliance_validator.py`)**
   - Enforces 10 distinct quality and campaign compliance checks:
     - `duration_check`: Verifies clip duration is within `[min_clip_duration, max_clip_duration]`.
     - `aspect_ratio_check`: Validates vertical 9:16 framing.
     - `resolution_check`: Validates minimum 1080x1920 resolution.
     - `video_integrity_check`: Probes for black frames, corruption, and frozen video.
     - `audio_integrity_check`: Verifies presence of non-silent normalized audio.
     - `required_hashtags_check`: Ensures all campaign hashtags are embedded in captions.
     - `required_mentions_check`: Verifies required sponsor/creator account mentions.
     - `platform_eligibility_check`: Validates target platform against campaign permissions.
     - `prohibited_editing_check`: Blocks disallowed edits (raw reuploads, watermarks).
     - `duplicate_check`: Detects identical or overlapping clip timestamps.

7. **Earnings Tracking Foundations (`app/services/earnings_tracker.py`)**
   - Tracks submission evidence, live post URLs, verified eligible views, and payout amounts.
   - **Honest Payout Ledger**: Strictly distinguishes `pending_rewards` from confirmed `approved_rewards`, preventing premature earnings reporting.
   - Tracks real-time progress toward a **$500 monthly target over 30 days**.

### Campaign API Endpoints

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/api/v1/campaigns/discover` | Discover active gaming campaigns from marketplaces |
| `POST` | `/api/v1/campaigns/import` | Import campaign via URL or raw operator rules |
| `POST` | `/api/v1/campaigns` | Register a new campaign with rule analysis |
| `GET` | `/api/v1/campaigns` | List campaigns with filters (`status`, `game`, `marketplace`) |
| `GET` | `/api/v1/campaigns/{id}` | Retrieve complete campaign details and raw rules |
| `POST` | `/api/v1/campaigns/{id}/analyze` | Re-analyze campaign eligibility and ambiguous rules |
| `POST` | `/api/v1/campaigns/{id}/review` | Operator approve or reject campaign |
| `POST` | `/api/v1/campaigns/{id}/sources` | Register authorized source footage with rights evidence |
| `GET` | `/api/v1/campaigns/{id}/sources` | List authorized footage associated with campaign |
| `POST` | `/api/v1/campaigns/{id}/jobs` | Enqueue clipping job through central eligibility gate |
| `GET` | `/api/v1/campaigns/{id}/clips` | List clips generated for campaign with compliance status |
| `GET` | `/api/v1/clips/{clip_id}/compliance` | Inspect 10-point compliance results for a clip |
| `POST` | `/api/v1/campaigns/{id}/submit` | Record live submission evidence for earnings verification |
| `GET` | `/api/v1/campaigns/earnings/summary` | Progress toward $500 30-day earnings target |
