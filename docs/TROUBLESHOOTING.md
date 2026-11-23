# Troubleshooting

Every failed job has an `error_code` and an `error_message` that are safe to show to users. Quote the `request_id` from error responses when you investigate in the logs.

## Job failure codes

| Code | Meaning | What to do |
| --- | --- | --- |
| `corrupt_media` | FFprobe or the decode check could not read the file | Re-export as MP4 (H.264 video, AAC audio) and upload again |
| `unreadable_media`, `empty_file` | The upload is unreadable or has no data | Upload the original export again |
| `no_video_stream` | The file has no video track | Upload a file with video |
| `invalid_dimensions` | FFprobe reported unusable frame dimensions | Re-export the video with normal width and height |
| `duration_below_minimum` / `duration_exceeds_limit` / `duration_unknown` | Source is too short, too long, or its length can't be read | Trim the source, or re-export with a readable duration |
| `source_missing`, `source_failed` | The stored source is gone or failed earlier | Upload the source again |
| `duplicate_source` | The same file is already stored for this owner | Reuse the existing source |
| `unavailable` | YouTube reports the video is private, removed, age-restricted, region-blocked or otherwise unavailable | Use a source you have rights to, or upload the file |
| `live_stream` | The URL is a live stream | Wait until it is a recorded video, or upload a file |
| `retrieval_disabled` | YouTube retrieval is off on this server | Enable it only for permitted sources, or upload the file |
| `retrieval_timeout`, `network_error` | YouTube or the network did not respond | Automatic retry where allowed; otherwise retry the job later |
| `insufficient_highlights` | Fewer than five moments scored at least `MIN_CLIP_SCORE` within the duration limits | Use longer or more substantive source material. The system never pads with weak moments |
| `no_speech_detected` | Whisper found no speech to analyse | Use a source with spoken audio |
| `transcription_failed` | Whisper could not transcribe the audio | Check the audio track is readable, then retry |
| `ai_not_configured` | No highlight-analysis provider is configured | Add `GEMINI_API_KEY` to the worker's environment |
| `ai_rate_limited`, `ai_unavailable`, `ai_timeout` | The AI provider throttled, was down or timed out after retries | Retry the job later |
| `ai_auth_failed` | The AI provider rejected the server's key | Check `GEMINI_API_KEY` is valid and enabled |
| `ai_invalid_response` | The AI provider's output failed validation after retries | Retry the job. If it repeats, report the `request_id` |
| `ffmpeg_render_failed`, `invalid_ffmpeg_args` | FFmpeg could not render a clip | Retry the job. If it repeats, report the `request_id` |
| `caption_sync_failed` | Captions could not be aligned with the clip's audio timing | Retry the job. If it repeats, report the `request_id` |
| `output_incomplete`, `output_validation_failed` | A rendered clip failed validation, so the job was not completed | Retry the job. Only fully validated sets of five clips are ever delivered |
| `storage_failed` | Storage refused or dropped a write | Automatic retry where allowed. If it persists, check the bucket and credentials |
| `worker_lost` | The worker stopped, or the job ran past `JOB_TIMEOUT_SECONDS`, and no attempts remain | Create a new job. Consider a shorter source |
| `cancelled` (status) | Cancelled by the owner | Create a new job if you still need the clips |

## API error codes you may see

| Code | Meaning |
| --- | --- |
| `auth_required`, `invalid_token`, `token_expired` | Missing, invalid or expired bearer token |
| `auth_not_configured` | The server has no `AUTH_JWT_SECRET`; fix the configuration |
| `admin_required` | The endpoint needs an admin subject or `X-Admin-Key` |
| `rate_limited` | More than `RATE_LIMIT_PER_MINUTE` requests in a minute. Wait and retry |
| `upload_too_large` | The file is above `MAX_UPLOAD_BYTES` |
| `unsupported_format`, `content_type_mismatch` | The extension, MIME type or file signature is not an accepted video |
| `unsupported_url`, `unsupported_host`, `invalid_link` | The URL isn't a supported YouTube video link |
| `source_not_ready` | The source isn't ready (still being checked, or it failed). Check `failure_code` on the source |
| `duplicate_job` | A conflicting job was created at the same moment | Check your job list for the existing job, or resend with the same `Idempotency-Key` to get it back |
| `invalid_idempotency_key` | The `Idempotency-Key` header is not 8 to 128 characters |
| `job_not_found`, `job_already_finished` | Unknown job for your account, or a job that can no longer be cancelled |
| `clip_not_ready` | The clip is not rendered and validated yet |
| `link_expired`, `link_not_supported` | A signed download link has expired or doesn't apply to this storage backend |
| `queue_unavailable`, `database_unavailable` | A dependency is temporarily down. Retry later |
| `database_not_configured`, `queue_not_configured` | The server is missing configuration; see `/ready` |

## Checking an unhealthy deployment

- **`/health` is 200 but `/ready` is 503:** the body names the failing check (`database`, `queue`, `storage` or `media_tools`) with its error. Usually a wrong `DATABASE_URL`, an unreachable Redis, a missing bucket, or FFmpeg missing from the image.
- **Jobs stay `queued`:** no worker is running, or the worker can't reach Redis. `GET /admin/diagnostics` shows heartbeats and queue depth.
- **Jobs stay in an active stage:** the worker stopped. Stale-heartbeat recovery requeues them after `STALE_HEARTBEAT_SECONDS`; if they don't move, check the worker logs for the job ID.
- **Migration fails during deploy:** the release stops before the new code starts. Fix the migration and redeploy. Applied migrations are not re-run.
- **Startup refuses to start:** the log lists every invalid setting at once. Fix them together.
