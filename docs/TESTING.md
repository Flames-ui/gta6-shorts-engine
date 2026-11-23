# Testing

```bash
pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/studio_test
pytest -q
```

Tests that need PostgreSQL use `TEST_DATABASE_URL`, and each creates and drops its own schema. Media tests need `ffmpeg` and `ffprobe` on `PATH`. Lint with `ruff check app tests`.

## What is covered

| Suite | Covers |
| --- | --- |
| `test_config.py`, `test_states.py` | Strict configuration checks (clip count fixed at 5, no fake AI in production, database URL form), job state transitions |
| `test_urls.py`, `test_youtube_policy.py` | YouTube URL parsing and host allowlist; yt-dlp is configured without cookies; restriction errors map to safe codes |
| `test_uploads.py`, `test_media_real.py` | Extension, MIME and container checks; real FFprobe and FFmpeg probing and rendering |
| `test_captions.py` | Caption timing, SRT/VTT/ASS output, presets |
| `test_selection.py`, `test_ai_analysis.py` | Score threshold (75), duration bounds, overlap merging, insufficient-highlights failure, AI response validation |
| `test_storage.py`, `test_storage_supabase.py` | Key validation, local storage, Supabase adapter via mocked HTTP (auth header, private bucket, failures, signed links) |
| `test_db_repository.py`, `test_migrations.py` | Repository behaviour, idempotent clip upserts, migration discovery and application |
| `test_api.py` | Authentication, owner isolation, rate limits, error envelope, every endpoint's happy and failure paths |
| `test_worker_recovery.py` | Stale-heartbeat recovery; one re-enqueue per queued job per hour |
| `test_pipeline_e2e.py` | Full pipeline with real FFmpeg and PostgreSQL: five distinct vertical clips produced, validated and served; insufficient-highlights and retry paths |

At the time of writing: **236 tests pass** against real PostgreSQL and FFmpeg.

## What is not covered by automated tests

- **Live Gemini calls.** The adapter is tested with mocked responses. Verify with your key in staging.
- **Live YouTube retrieval.** The policy and error mapping are tested; no live video download is part of the suite, and none should be run against content you lack rights to.
- **Whisper transcription quality.** The pipeline is tested with a transcript fixture. Run it on representative audio before relying on timing.
- **Supabase Storage in production.** Verified against a mocked transport only.
- **Multi-worker behaviour under load.** Locks and recovery are tested in isolation.

Run the production smoke test in [DEPLOYMENT.md](DEPLOYMENT.md#verifying-a-deployment) after every deploy.
