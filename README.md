# GTA 6 Shorts Engine — Production Backend & Render Deployment

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python)](https://python.org)
[![Supabase](https://img.shields.io/badge/Supabase-Database%20%26%20Storage-3ECF8E?style=flat-square&logo=supabase)](https://supabase.com)
[![Redis RQ](https://img.shields.io/badge/Queue-Redis%20RQ-DC382D?style=flat-square&logo=redis)](https://python-rq.org)
[![Render](https://img.shields.io/badge/Deploy-Render%20Blueprint-46E3B7?style=flat-square&logo=render)](https://render.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A production-grade, asynchronous video processing engine engineered for generating high-engagement, viral vertical shorts (9:16) from long-form YouTube content and authorized video sources. Powered by Google Gemini 2.5 Flash / OpenAI for highlight detection, Whisper speech-to-text transcription, FFmpeg vertical framing, Supabase cloud persistence, Redis RQ background workers, and automated YouTube publishing workflows.

Connected natively to Lovable web dashboards.

---

## 1. Architecture Overview

```
                      ┌───────────────────────────────────────────────┐
                      │            Lovable Frontend App               │
                      │  (Dashboard, Queue, Player, Approval Flow)   │
                      └───────────────────────┬───────────────────────┘
                                              │ REST API (HTTPS + X-API-Key)
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                Render Deployment Cloud                                  │
│                                                                                         │
│  ┌────────────────────────────────┐                 ┌────────────────────────────────┐  │
│  │   gta6-shorts-engine (Web)     │                 │   gta6-shorts-worker (Worker)  │  │
│  │   - FastAPI HTTP Server        │                 │   - Dedicated high-RAM/CPU    │  │
│  │   - Pydantic Validation        │                 │   - FFmpeg 8.x + Whisper       │  │
│  │   - SSRF Protection            │                 │   - Gemini AI Highlights       │  │
│  │   - Job & Auth Management      │                 │   - Dynamic Social Metadata    │  │
│  └───────────────┬────────────────┘                 └────────────────┬───────────────┘  │
│                  │                                                   │                  │
│                  │           ┌────────────────────────┐              │                  │
│                  └──────────►│  Render Redis Queue    │◄─────────────┘                  │
│                     Enqueue  │  (gta6_shorts default) │   Dequeue & Process             │
│                              └────────────────────────┘                                 │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              External Cloud Services                                    │
│                                                                                         │
│  ┌───────────────────────────────────────────┐   ┌───────────────────────────────────┐  │
│  │           Supabase Cloud                  │   │        Google / AI APIs           │  │
│  │   - Postgres: jobs, clips, logs           │   │   - Gemini 2.5 Flash / OpenAI     │  │
│  │   - Storage: 9:16 rendered MP4 shorts     │   │   - YouTube Data API v3 (OAuth)   │  │
│  │   - Row Level Security (RLS)              │   │     (Private-by-default shorts)   │  │
│  └───────────────────────────────────────────┘   └───────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### Architectural Principles:
1. **Separation of Concerns**: FastAPI handlers respond in `<50ms` and never perform heavy video encoding. All downloads, AI calls, Whisper transcriptions, and FFmpeg renders are executed asynchronously in isolated background worker processes.
2. **SSRF & Input Hardening**: Strict URL allowlisting permits only verified YouTube domains (`youtube.com`, `youtu.be`) and rejects arbitrary local file paths, private IP addresses (`10.0.0.0/8`, `192.168.0.0/16`, `169.254.169.254`), and non-HTTP protocols.
3. **Stateless Compute with Cloud Persistence**: Neither the web service nor the background worker relies on ephemeral disk for permanent storage. Rendered clips and thumbnails are immediately uploaded to Supabase Storage, and processing metadata is saved to PostgreSQL.
4. **Resilient Failovers**: If Supabase or Redis is temporarily unavailable during local development or unit testing, the system automatically uses an ACID-compliant SQLite fallback and in-process threaded job execution.

---

## 2. Local Setup Instructions

### Prerequisites
- Python 3.11+
- FFmpeg 6.0+ installed and available on `PATH`
- Redis (optional for local queue testing, defaults to thread fallback if absent)

### Step 1: Clone and Create Virtual Environment
```bash
git clone https://github.com/Flames-ui/gta6-shorts-engine.git
cd gta6-shorts-engine

python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
```bash
cp .env.example .env
# Edit .env and supply your GEMINI_API_KEY, SUPABASE_URL, and ENGINE_API_KEY
```

### Step 4: Run the Services Locally

**Option A: Run the FastAPI Web Server**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Access the interactive Swagger documentation at `http://localhost:8000/docs`.

**Option B: Run the Background Worker**
```bash
python3 -m app.worker
```

**Option C: Run the Original CLI**
```bash
# Local mode with Gemini 2.5 Flash highlight extraction
python3 main.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
    --mode local \
    --llm-provider gemini \
    --num-clips 3 \
    --aspect-ratio 9:16
```

### Step 5: Run Automated Tests
```bash
pytest -v
```

---

## 3. Environment Variables Reference

| Variable | Required | Description | Example |
|---|---|---|---|
| `ENGINE_API_KEY` | **Yes (Prod)** | Secret API key required in `X-API-Key` or `Authorization: Bearer` header. | `sec_gta6_live_98a7bc...` |
| `GEMINI_API_KEY` | **Yes** (if using Gemini) | Google Gemini API key for intelligent highlight ranking & social copy. | `AIzaSy...` |
| `OPENAI_API_KEY` | Optional | OpenAI API key (used if `LLM_PROVIDER=openai`). | `sk-proj-...` |
| `LLM_PROVIDER` | No | Default highlight ranking LLM: `gemini` or `openai` (default: `gemini`). | `gemini` |
| `SUPABASE_URL` | **Yes (Prod)** | Your Supabase project URL. | `https://xyzproject.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | **Yes (Prod)** | Supabase service-role key for backend database & storage access. | `eyJhbGciOi...` |
| `SUPABASE_STORAGE_BUCKET` | No | Supabase Storage bucket for rendered MP4 clips (default: `shorts`). | `shorts` |
| `REDIS_URL` | **Yes (Prod)** | Redis connection string for background queue. | `redis://default:pass@redis.render.com:6379` |
| `CORS_ORIGINS` | No | Comma-separated list of allowed frontend dashboard origins. | `http://localhost:5173,https://*.lovable.app` |
| `MAX_VIDEO_DURATION_SECONDS`| No | Hard cutoff for input video duration (default: 7200 = 2 hours). | `7200` |
| `MAX_CONCURRENT_JOBS` | No | Worker concurrency limit (default: `2`). | `2` |
| `JOB_TIMEOUT_SECONDS` | No | Timeout before a stuck job is aborted (default: `1800` = 30m). | `1800` |
| `YOUTUBE_CLIENT_ID` | Optional | Google Cloud OAuth 2.0 Client ID for YouTube Shorts upload. | `123456789.apps.googleusercontent.com` |
| `YOUTUBE_CLIENT_SECRET` | Optional | Google Cloud OAuth 2.0 Client Secret. | `GOCSPX-...` |
| `YOUTUBE_REDIRECT_URI` | Optional | OAuth callback redirect URL. | `https://engine.onrender.com/api/v1/auth/youtube/callback` |
| `ENVIRONMENT` | No | `production`, `development`, or `test`. | `production` |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, or `ERROR` (default: `INFO`). | `INFO` |

---

## 4. Supabase Setup & SQL Migrations

The engine uses Supabase for database persistence and public video asset storage.

### 1. Apply Database Schema
Execute the SQL migration located in `supabase/migrations/20261121000000_initial_schema.sql` in your **Supabase SQL Editor**:
- Creates `jobs`, `clips`, `source_videos`, and `job_logs` tables.
- Creates optimized indexes on `url`, `status`, `job_id`, and `created_at`.
- Sets up Row Level Security (RLS) policies allowing secure backend read/write access.

### 2. Configure Supabase Storage Bucket
1. In the Supabase dashboard, navigate to **Storage**.
2. Create a new bucket named **`shorts`** (or the value set in `SUPABASE_STORAGE_BUCKET`).
3. Set bucket access to **Public** so generated clip URLs can be streamed by the Lovable dashboard.

---

## 5. Render Deployment Instructions

The repository includes a ready-to-deploy `render.yaml` Blueprint specification defining two isolated services and a managed Redis instance.

### Automatic Blueprint Deployment
1. Log in to [Render](https://dashboard.render.com/).
2. Click **New +** → **Blueprint**.
3. Connect your GitHub repository `Flames-ui/gta6-shorts-engine`.
4. Render will parse `render.yaml` and configure:
   - **`gta6-shorts-engine`** (Web Service):
     - Docker runtime using the repository `Dockerfile`.
     - Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 2`
     - Health Check Path: `/health`
   - **`gta6-shorts-worker`** (Background Worker):
     - Docker runtime with FFmpeg 8.x, Python 3.11, and Whisper pre-installed.
     - Plan: `standard` (2 GB RAM, 1 CPU) or higher for video encoding.
     - Command: `python3 -m app.worker`
   - **`gta6-shorts-redis`** (Redis):
     - Private networking queue for reliable job dispatching.
5. Provide the required secrets in the Render dashboard:
   - `ENGINE_API_KEY`
   - `GEMINI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
6. Click **Apply**.

---

## 6. Complete API Reference

All protected endpoints require authentication via:
`X-API-Key: <ENGINE_API_KEY>` or `Authorization: Bearer <ENGINE_API_KEY>`

### 1. Health & Status
#### `GET /health`
Liveness and readiness probe for load balancers and Render health checks.
```json
{
  "status": "healthy",
  "app": "GTA 6 Shorts Engine",
  "version": "1.0.0",
  "timestamp": "2026-11-21T12:00:00Z",
  "redis": "connected",
  "supabase": "connected"
}
```

#### `GET /api/v1/status`
Engine operational status, queue depth, and worker count.
```json
{
  "status": "operational",
  "queue_depth": 1,
  "active_workers": 2,
  "gemini_configured": true,
  "supabase_configured": true,
  "youtube_oauth_configured": false
}
```

### 2. Job Management
#### `POST /api/v1/jobs`
Submit a new video processing job. Duplicate URLs with active jobs return `409 Conflict` unless `"force": true` is specified.
```json
// Request Body
{
  "url": "https://www.youtube.com/watch?v=QdBZY2fkU-0",
  "num_clips": 3,
  "aspect_ratio": "9:16",
  "mode": "local",
  "llm_provider": "gemini",
  "gta_preset": true,
  "force": false
}

// Response (201 Created)
{
  "id": "job_a1b2c3d4e5f6",
  "url": "https://www.youtube.com/watch?v=QdBZY2fkU-0",
  "status": "queued",
  "stage": "pending",
  "progress": 0,
  "num_clips": 3,
  "video_title": null,
  "video_duration": null,
  "error": null,
  "mode": "local",
  "llm_provider": "gemini",
  "gta_preset": true,
  "retry_count": 0,
  "created_at": "2026-11-21T12:01:00Z",
  "clips": [],
  "logs": []
}
```

#### `GET /api/v1/jobs/{job_id}`
Retrieve real-time processing status, progress percentage, log events, and generated clips.
```json
{
  "id": "job_a1b2c3d4e5f6",
  "status": "processing",
  "stage": "transcribing",
  "progress": 35,
  "video_title": "Grand Theft Auto VI — Official Trailer 1",
  "video_duration": 91.0,
  "clips": [],
  "logs": [
    {"timestamp": "2026-11-21T12:01:05Z", "stage": "downloading", "message": "Source video acquired"},
    {"timestamp": "2026-11-21T12:01:25Z", "stage": "transcribing", "message": "Whisper speech-to-text complete"}
  ]
}
```

#### `GET /api/v1/jobs`
List jobs with pagination and status filtering.
```
GET /api/v1/jobs?limit=20&offset=0&status=completed
```

#### `POST /api/v1/jobs/{job_id}/cancel`
Cancel an active queued or processing job.

#### `POST /api/v1/jobs/{job_id}/retry`
Re-enqueue a failed or cancelled job.

### 3. Generated Clips & Publishing
#### `GET /api/v1/clips/{clip_id}`
Retrieve a single generated Short with AI-generated titles, hooks, viral scores, and storage stream URL.
```json
{
  "id": "clip_789abc",
  "job_id": "job_a1b2c3d4e5f6",
  "title": "GTA 6 Vice City Beach Scene Breakdown",
  "hook_sentence": "Look closely at the water physics in this scene!",
  "score": 94,
  "start_time": 12.5,
  "end_time": 42.5,
  "duration": 30.0,
  "storage_url": "https://xyz.supabase.co/storage/v1/object/public/shorts/clips/clip_789abc.mp4",
  "aspect_ratio": "9:16",
  "publishing_status": "draft",
  "suggested_tags": ["GTA6", "GrandTheftAuto", "Shorts", "ViceCity", "Gaming"]
}
```

#### `POST /api/v1/clips/{clip_id}/approve`
Human-in-the-loop approval endpoint. Must be called before any clip can be published.
```json
{
  "approved_for_publishing": true,
  "custom_title": "Official GTA 6 Vice City Stunts #Shorts",
  "custom_description": "Insane GTA 6 moments captured in 4K 60FPS.",
  "custom_hashtags": ["GTA6", "Shorts", "Gaming"]
}
```

#### `POST /api/v1/clips/{clip_id}/publish`
Uploads the approved clip to YouTube as a **private-by-default** Short. Never releases clips publicly without explicit human intervention.

---

## 7. Lovable Dashboard Integration Guide

The GTA 6 Shorts Engine is structured for seamless integration with Lovable-built frontend applications.

### Recommended UI Screens & Views:
1. **Source Channel Monitor & Ingestion**:
   - URL input card with immediate regex / domain feedback.
   - Presets for clip counts (1, 3, 5) and GTA-virality tuning.
2. **Processing Pipeline & Live Progress Bar**:
   - Visual step indicator: `Pending` → `Downloading` → `Transcribing` → `AI Analysis` → `FFmpeg Crop` → `Uploading` → `Completed`.
   - Polls `GET /api/v1/jobs/{job_id}` at a 3-second interval until terminal status.
3. **Shorts Gallery & Video Player**:
   - 9:16 vertical video player rendering `storage_url`.
   - Card displays viral score badge (0–100), hook sentence, and suggested hashtags.
4. **Publishing Approval Workflow**:
   - Editable fields for Title, Description, and Tags.
   - "Approve for Publishing" action button triggers `POST /api/v1/clips/{clip_id}/approve`.
   - "Publish to YouTube" triggers `POST /api/v1/clips/{clip_id}/publish`.

---

## 8. YouTube Publishing & Compliance Architecture

- **Private-by-Default Policy**: All automated publishing requests explicitly set `"privacyStatus": "private"`. No clip is ever made public automatically.
- **Copyright & Compliance Notice**: The backend enforces modular authorized source ingestion. Utilizing automated tooling does not override YouTube's Terms of Service or third-party intellectual property rights. Users must possess appropriate rights or licenses for all processed media.
- **OAuth Token Handling**: YouTube Data API v3 OAuth refresh tokens are stored securely in Supabase with database-level encryption and are never returned in client API responses.

---

## 9. Troubleshooting & Common Issues

| Issue | Cause | Solution |
|---|---|---|
| `503 Service Unavailable` on health check | Redis connection failed | Verify `REDIS_URL` in environment variables or check private networking hostname. |
| `401 Unauthorized` on API requests | Missing or wrong API key | Pass `X-API-Key: <ENGINE_API_KEY>` header. |
| `400 Bad Request: URL must be an approved YouTube domain` | Input URL failed security check | Only valid HTTPS YouTube URLs (`youtube.com`, `youtu.be`) are accepted. |
| Worker OOM (Out of Memory) crash | Render worker instance memory too low | Increase Background Worker plan to at least 2 GB RAM in `render.yaml` or Render dashboard. |
| Gemini highlight detection error | Missing or invalid Gemini API key | Ensure `GEMINI_API_KEY` is set and has active quota. |
| SQLite locking errors in local testing | Multiple processes accessing SQLite | Concurrency locking is handled via `threading.RLock()`. In production, configure Supabase PostgreSQL. |

---

## 10. License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
