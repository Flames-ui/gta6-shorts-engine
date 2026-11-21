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
│  ┌────────────────────────────────┐                 ┌────────────────────────────────┐  │
│  │   gta6-shorts-engine (Web)     │                 │   gta6-shorts-worker (Worker)  │  │
│  │   - FastAPI HTTP Server        │                 │   - Dedicated high-RAM/CPU    │  │
│  │   - Pydantic v2 Validation     │                 │   - FFmpeg 8.x + Whisper       │  │
│  │   - SSRF & Safe Host Filtering │                 │   - Gemini 2.5 Flash Highlights│  │
│  │   - Rate Limiting & Auth Gates │                 │   - Vertical 9:16 Crop/Reframe │  │
│  └───────────────┬────────────────┘                 └────────────────┬───────────────┘  │
│                  │                                                   │                  │
│                  │           ┌────────────────────────┐              │                  │
│                  └──────────►│  Render Redis Queue    │◄─────────────┘                  │
│                     Enqueue  │  (Redis RQ gta6_shorts)│   Dequeue & Process             │
│                              └────────────────────────┘                                 │
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

The repository includes a ready-to-deploy `render.yaml` Blueprint configuring three linked services:

1. **`gta6-shorts-engine`** (Web Service):
   - Fast lightweight FastAPI application.
   - Handles API traffic, validation, Supabase synchronization, and queue dispatch.
   - Health check path: `/health`.

2. **`gta6-shorts-worker`** (Background Worker):
   - High-CPU/RAM container with pre-installed FFmpeg and Whisper models.
   - Dequeues jobs from Redis and executes AI highlighting, audio extraction, vertical cropping, and video validation.

3. **`gta6-shorts-redis`** (Private Redis):
   - High-performance job queue for Redis RQ.

To deploy, connect your GitHub repository to [Render](https://render.com) and deploy the Blueprint directly via `render.yaml`.

---

## 11. Lovable Dashboard Integration & API Contracts

Lovable dashboards connect to the backend using standard HTTPS requests with the `X-API-Key: <ENGINE_API_KEY>` header:

### Key Endpoints for Lovable:
- `GET /health`: Service and dependency status.
- `GET /api/v1/categories`: Retrieve all categories for selection dropdowns.
- `POST /api/v1/jobs`: Create video processing job (`url`, `category_id`, `num_clips`, `aspect_ratio`).
- `GET /api/v1/jobs/{job_id}`: Poll job progress and status (`queued`, `downloading`, `transcribing`, `analyzing`, `rendering`, `completed`, `failed`).
- `GET /api/v1/clips/{clip_id}`: Retrieve clip details, signed video URL, and generated hooks.
- `POST /api/v1/clips/{clip_id}/approve`: Mark a clip as approved for social publishing.
- `POST /api/v1/clips/{clip_id}/schedule`: Submit approved clip to Buffer for scheduled release.
- `GET /api/v1/analytics/overview`: High-level multi-platform performance metrics.

---

## 12. Troubleshooting & Verification

- **Redis Offline in Development**: In development or test mode, the system safely falls back to local in-process execution for developer convenience. In production, it fails closed to protect workload integrity.
- **Missing AI Key**: If Gemini/OpenAI API keys are not supplied, the pipeline activates heuristic highlight fallback mode to allow end-to-end rendering test execution without crashing.
- **Clip Validation Failures**: Videos failing minimum length, audio presence, or resolution checks are logged and quarantined rather than published.
