# API reference

Base URL: `https://<host>/api/v1`. Health endpoints live at the root.

## Authentication

Every endpoint under `/api/v1` except `GET /files/{token}` requires `Authorization: Bearer <JWT>`.

- Algorithm: **HS256**, verified with `AUTH_JWT_SECRET`.
- Audience: `AUTH_JWT_AUDIENCE` (default `authenticated`). Expiry is enforced, with `AUTH_TOKEN_LEEWAY_SECONDS` of clock skew allowed.
- Subject (`sub`) is the owner ID. Every source, job, clip and download is scoped to it. Owners cannot see each other's data; a request for someone else's resource returns `404`.
- Admin endpoints require an admin subject (`ADMIN_USER_IDS`) or the operator key in `X-Admin-Key` (`ADMIN_API_KEY`).

## Errors

Every error uses one envelope:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Too many requests. Wait a minute and try again.",
    "details": {},
    "request_id": "9f0c2a7e-..."
  }
}
```

Messages are written for end users and never include stack traces, file paths, provider keys or database errors. Keep `request_id` when reporting a problem.

## Health

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness: the process is running. No dependencies checked. |
| GET | `/ready` | Readiness: database, queue, storage and media tools are usable. Returns `200` when all are, `503` with per-check details when any is not. |

## Sources

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/sources/validate` | Checks a YouTube URL's format only. No network access. |
| POST | `/sources/youtube` | Registers a YouTube URL. Metadata is fetched now when retrieval is enabled. Returns `201`. |
| POST | `/sources/upload` | `multipart/form-data` with `file`. MP4, M4V, MOV, MKV or WebM; size capped by `MAX_UPLOAD_BYTES`. Returns `201`. |
| GET | `/sources/{source_id}` | One of your sources. |

Accepted YouTube hosts: `youtube.com`, `www.youtube.com`, `m.youtube.com`, `music.youtube.com`, `youtu.be`.

Example request:

```json
{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }
```

Source object (abridged):

```json
{
  "id": "5f1d6c8e-...",
  "source_type": "upload",
  "status": "ready",
  "title": "Founder talk",
  "duration_seconds": 1312.4,
  "width": 1920,
  "height": 1080,
  "has_audio": true,
  "container_format": "mov,mp4,m4a,3gp,3g2,mj2",
  "file_size_bytes": 412883456,
  "sha256": "3a7bd3e2...",
  "failure_code": null,
  "failure_message": null
}
```

Durations, dimensions and hashes come from FFprobe and the file bytes. They are never estimated.

## Jobs

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/jobs` | Creates and queues a job. Returns `202` once it is saved and queued. |
| GET | `/jobs?limit=&offset=` | Your jobs, newest first. |
| GET | `/jobs/{job_id}` | Status, stage, progress, clips and recent events. |
| POST | `/jobs/{job_id}/cancel` | Requests cancellation of a queued or running job. |
| GET | `/jobs/{job_id}/clips` | The clips a job produced. |

Create request:

```json
{
  "source_id": "5f1d6c8e-...",
  "clip_count": 5,
  "caption_preset": "bold_creator",
  "language": null,
  "min_clip_seconds": 15,
  "max_clip_seconds": 90
}
```

- `clip_count` must be exactly `5`.
- `caption_preset`: `clean_minimal`, `bold_creator`, `high_contrast`, `modern_short` or `highlight_keywords`.
- `language`: optional ISO code. Omit it to let Whisper detect the language.

**Idempotency:** send an `Idempotency-Key` header (8 to 128 characters) to make creation safe to retry. A repeated key returns the job already created, with `200` instead of `202`. Keys are scoped to your owner ID.

Job object (abridged):

```json
{
  "id": "b2c4...",
  "source_id": "5f1d6c8e-...",
  "status": "rendering",
  "stage": "rendering",
  "progress": 72,
  "clip_count": 5,
  "aspect_ratio": "9:16",
  "caption_preset": "bold_creator",
  "cancel_requested": false,
  "attempt_count": 1,
  "error_code": null,
  "error_message": null,
  "completed_at": null
}
```

`status` and `stage` carry the same value, which is the job's current point in the pipeline:

- `queued`: waiting for a worker.
- Active stages, in order: `validating`, `retrieving` (YouTube only), `uploading_source`, `analyzing`, `transcribing`, `selecting_highlights`, `extracting_clips`, `rendering`, `uploading_outputs`, `validating_outputs`.
- Terminal: `completed`, `failed`, `cancelled`.

`progress` is 0 to 100 and rises with each stage. `attempt_count` shows how many times a worker has started the job.

A job reaches `completed` only when five clips exist, each has a stored MP4 and passed output validation. A job that cannot meet that bar ends `failed` with an `error_code` such as `insufficient_highlights`.

## Clips

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/clips/{clip_id}` | Clip details, validation result and caption formats. |
| GET | `/clips/{clip_id}/download-url` | A signed URL that expires after `SIGNED_URL_TTL_SECONDS` (default 300). |
| GET | `/clips/{clip_id}/download` | Streams the MP4 with your authentication. |
| GET | `/clips/{clip_id}/captions/{fmt}` | `fmt` is `srt`, `vtt` or `ass`. |

Clip object (abridged):

```json
{
  "id": "e91a...",
  "job_id": "b2c4...",
  "clip_number": 2,
  "title": "The pricing mistake most founders make",
  "start_seconds": 412.0,
  "end_seconds": 441.6,
  "duration_seconds": 29.6,
  "width": 1080,
  "height": 1920,
  "render_status": "completed",
  "validation_status": "passed"
}
```

Signed download links are time-limited and bound to one object. `GET /files/{token}` serves them when storage is local; with Supabase Storage the link points at Supabase directly.

## Usage

`GET /usage?days=30` returns your job counts by status and processing totals for the window. `days` is 1 to 365 (default 30).

## Admin

`GET /admin/diagnostics` returns job counts by status, worker heartbeats, queue depth and a configuration summary (no secrets). Requires an admin subject or `X-Admin-Key`.

## Rate limits

Authenticated requests are limited to `RATE_LIMIT_PER_MINUTE` (default 120) per owner. Exceeding it returns `429` with code `rate_limited`.
