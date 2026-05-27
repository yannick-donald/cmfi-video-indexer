from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from googleapiclient.errors import HttpError

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def execute_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 6,
    base_delay: float = 1.0,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except HttpError as exc:
            last_error = exc
            status = getattr(exc.resp, "status", None)
            if status not in RETRYABLE_STATUS or attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            LOGGER.warning(
                "Drive API error %s — retrying in %.1fs (attempt %s/%s)",
                status,
                delay,
                attempt,
                max_attempts,
            )
            time.sleep(delay)
        except Exception:
            raise
    if last_error:
        raise last_error
    raise RuntimeError("execute_with_retry failed without exception")
