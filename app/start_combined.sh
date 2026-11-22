#!/usr/bin/env bash
# =============================================================================
# Combined Process Supervisor: FastAPI Web Service + Redis RQ Worker
#
# Launches both the FastAPI HTTP server (Uvicorn) and the background worker
# (python -m app.worker) inside a single container instance to minimize costs.
#
# Supervises both processes:
# - Forwards SIGTERM / SIGINT for graceful shutdown of both processes.
# - Automatically terminates the surviving process if either crashes.
# - Propagates nonzero exit codes so Render can detect failures and restart.
# - Keeps all standard output and error unbuffered for instant Render logging.
# =============================================================================

set -Eeuo pipefail

# Force unbuffered Python stdout/stderr for immediate log streaming in Render
export PYTHONUNBUFFERED=1

WORKER_PID=""
WEB_PID=""
TERMINATING=0
EXIT_CODE=0

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [supervisor] $*"
}

cleanup() {
    local reason="${1:-SIGTERM}"
    if [ "$TERMINATING" -eq 1 ]; then
        return
    fi
    TERMINATING=1
    log "Shutdown initiated (reason: ${reason}). Stopping supervised child processes..."

    # Reset signal traps to avoid recursion during shutdown
    trap - SIGTERM SIGINT

    # Signal worker process
    if [ -n "$WORKER_PID" ] && kill -0 "$WORKER_PID" 2>/dev/null; then
        log "Sending SIGTERM to worker process (PID: ${WORKER_PID})..."
        kill -TERM "$WORKER_PID" 2>/dev/null || true
    fi

    # Signal web API process
    if [ -n "$WEB_PID" ] && kill -0 "$WEB_PID" 2>/dev/null; then
        log "Sending SIGTERM to web API process (PID: ${WEB_PID})..."
        kill -TERM "$WEB_PID" 2>/dev/null || true
    fi

    # Wait for worker process to exit gracefully (up to 15 seconds)
    if [ -n "$WORKER_PID" ]; then
        local count=0
        while kill -0 "$WORKER_PID" 2>/dev/null && [ "$count" -lt 15 ]; do
            sleep 1
            count=$((count + 1))
        done
        if kill -0 "$WORKER_PID" 2>/dev/null; then
            log "Worker process did not exit within 15s; sending SIGKILL..."
            kill -KILL "$WORKER_PID" 2>/dev/null || true
        fi
        wait "$WORKER_PID" 2>/dev/null || true
    fi

    # Wait for web API process to exit gracefully (up to 15 seconds)
    if [ -n "$WEB_PID" ]; then
        local count=0
        while kill -0 "$WEB_PID" 2>/dev/null && [ "$count" -lt 15 ]; do
            sleep 1
            count=$((count + 1))
        done
        if kill -0 "$WEB_PID" 2>/dev/null; then
            log "Web API process did not exit within 15s; sending SIGKILL..."
            kill -KILL "$WEB_PID" 2>/dev/null || true
        fi
        wait "$WEB_PID" 2>/dev/null || true
    fi

    log "All supervised child processes have stopped. Exiting supervisor with code ${EXIT_CODE}."
    exit "$EXIT_CODE"
}

# Trap termination signals from host / container runtime
trap 'EXIT_CODE=0; cleanup SIGTERM' SIGTERM
trap 'EXIT_CODE=0; cleanup SIGINT' SIGINT

log "Starting combined single-instance container..."
log "Service topology: FastAPI Web API + Redis RQ Worker in a single Render Web Service"
log "NOTE: CPU, memory, FFmpeg rendering, Whisper transcription, and API traffic share this single instance."

# 1. Start Redis RQ Worker in background
python -m app.worker &
WORKER_PID=$!
log "Started background worker (PID: ${WORKER_PID}) on queue '${QUEUE_NAME:-viral-shorts}'"

# 2. Start Uvicorn FastAPI Server in background
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
WEB_PID=$!
log "Started web API (PID: ${WEB_PID}) listening on 0.0.0.0:${PORT:-8000}"

# 3. Supervise processes: wait for either process to exit
set +e
wait -n -p EXITED_PID
FIRST_STATUS=$?
set -e

log "Supervised process ${EXITED_PID:-unknown} exited with status ${FIRST_STATUS}"

if [ "$FIRST_STATUS" -ne 0 ]; then
    EXIT_CODE="$FIRST_STATUS"
else
    # In a long-running service, an early exit of either process is abnormal
    EXIT_CODE=1
fi

cleanup "PROCESS_EXIT_${EXITED_PID:-UNKNOWN}"
