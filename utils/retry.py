from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
from typing import TypeVar

from googleapiclient.errors import HttpError

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Drive reports quota exhaustion as 403 with one of these reasons. A plain 403
# means "not allowed" and must not be retried, so the reason decides.
RETRYABLE_403_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "quotaExceeded",
    "sharingRateLimitExceeded",
    "backendError",
    "internalError",
}

# Transient transport failures worth another attempt.
RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    BrokenPipeError,
)

MAX_DELAY_SECONDS = 64.0


def _error_reasons(exc: HttpError) -> set[str]:
    """Pull the per-error 'reason' codes out of a Drive JSON error body."""
    try:
        payload = json.loads(exc.content.decode("utf-8", errors="replace"))
    except (AttributeError, ValueError):
        return set()
    errors = payload.get("error", {})
    if not isinstance(errors, dict):
        return set()
    reasons = {
        str(item.get("reason", ""))
        for item in errors.get("errors", [])
        if isinstance(item, dict)
    }
    if isinstance(errors.get("status"), str):
        reasons.add(errors["status"])
    return {reason for reason in reasons if reason}


def _is_retryable(exc: HttpError) -> bool:
    status = getattr(exc.resp, "status", None)
    if status in RETRYABLE_STATUS:
        return True
    if status == 403:
        return bool(_error_reasons(exc) & RETRYABLE_403_REASONS)
    return False


def _retry_after_seconds(exc: HttpError) -> float | None:
    """Honour a Retry-After header, given either as seconds or an HTTP date."""
    headers = getattr(exc, "resp", None)
    raw = headers.get("retry-after") if headers is not None else None
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _backoff_delay(attempt: int, base_delay: float) -> float:
    """
    Exponential backoff with equal jitter.

    Half the window is fixed and half is random, so concurrent workers spread
    out instead of retrying in lockstep, while still always waiting a while.
    """
    window = min(base_delay * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)
    return window / 2 + random.uniform(0, window / 2)


def execute_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 6,
    base_delay: float = 1.0,
) -> T:
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except HttpError as exc:
            if not _is_retryable(exc) or attempt == max_attempts:
                raise
            status = getattr(exc.resp, "status", None)
            delay = _retry_after_seconds(exc)
            source = "Retry-After"
            if delay is None:
                delay = _backoff_delay(attempt, base_delay)
                source = "backoff"
            delay = min(delay, MAX_DELAY_SECONDS)
            LOGGER.warning(
                "Drive API error %s — retrying in %.1fs (%s, attempt %s/%s)",
                status,
                delay,
                source,
                attempt,
                max_attempts,
            )
            time.sleep(delay)
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt == max_attempts:
                raise
            delay = _backoff_delay(attempt, base_delay)
            LOGGER.warning(
                "Drive API transport error (%s) — retrying in %.1fs (attempt %s/%s)",
                type(exc).__name__,
                delay,
                attempt,
                max_attempts,
            )
            time.sleep(delay)

    raise RuntimeError("execute_with_retry exhausted all attempts without returning")
