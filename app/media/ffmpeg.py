"""Safe FFmpeg / ffprobe execution.

* Arguments are always a list (never a shell string).
* Every run has a hard timeout and can be cancelled via a callback.
* Only the last part of stderr is kept, so logs stay bounded.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Callable, List, Optional

from ..errors import PipelineError
from ..config import settings

logger = logging.getLogger("studio.ffmpeg")
FFMPEG = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BINARY", "ffprobe")


class FFmpegResult:
    def __init__(self, returncode: int, stdout: str, stderr: str, seconds: float):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.seconds = seconds


class CancelledError(Exception):
    pass


_FILE_READING_FILTER = re.compile(r"(^|[=,;\[\s])a?movie\s*=", re.IGNORECASE)


def _check_args(args: List[str]) -> None:
    if not args or not all(isinstance(a, str) and "\x00" not in a for a in args):
        raise PipelineError("invalid_ffmpeg_args", "Internal media command was malformed.")
    for arg in args:
        # Inputs are local files we created. Network protocols and filters that read arbitrary
        # files (movie/amovie) are never legitimate here, so refuse them outright.
        if "://" in arg or _FILE_READING_FILTER.search(arg):
            raise PipelineError("invalid_ffmpeg_args", "Internal media command contained a disallowed input.")


def run(
    binary: str,
    args: List[str],
    *,
    timeout: Optional[int] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    label: str = "ffmpeg",
) -> FFmpegResult:
    _check_args(args)
    limit = timeout or settings.FFMPEG_TIMEOUT_SECONDS
    cmd = [binary, *args]
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=False,
    )
    last_cancel_poll = 0.0
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                if elapsed > limit:
                    proc.kill()
                    proc.communicate()
                    logger.error("%s timed out after %ss", label, limit)
                    raise PipelineError("media_timeout", f"Media processing timed out after {limit} seconds.",
                                        retryable=True)
                if cancel_check and time.monotonic() - last_cancel_poll > 2.0:
                    last_cancel_poll = time.monotonic()
                    if cancel_check():
                        proc.kill()
                        proc.communicate()
                        raise CancelledError()
    finally:
        if proc.poll() is None:
            proc.kill()
    stderr = (err or b"").decode("utf-8", "replace")[-4000:]
    result = FFmpegResult(proc.returncode, (out or b"").decode("utf-8", "replace"), stderr,
                          time.monotonic() - started)
    if result.returncode != 0:
        logger.warning("%s exited with %s", label, result.returncode, extra={"stderr_tail": stderr[-800:]})
    return result


def ffmpeg(args: List[str], **kwargs) -> FFmpegResult:
    return run(FFMPEG, ["-hide_banner", "-nostdin", "-loglevel", "error", *args], **kwargs)


def ffprobe(args: List[str], **kwargs) -> FFmpegResult:
    return run(FFPROBE, ["-v", "error", *args], label="ffprobe", **kwargs)
