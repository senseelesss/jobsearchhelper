"""Bounded, content-free latency samples; never retain SQL, IDs or payloads."""

import logging
import math
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from functools import wraps

logger = logging.getLogger(__name__)
_samples = defaultdict(lambda: deque(maxlen=512))
_lock = threading.Lock()


def record(stage, seconds):
    milliseconds = max(0.0, seconds * 1000)
    with _lock:
        _samples[stage].append(milliseconds)
    logger.debug("telegram_timing stage=%s duration_ms=%.3f", stage, milliseconds)


@contextmanager
def span(stage):
    start = time.perf_counter()
    try:
        yield
    finally:
        record(stage, time.perf_counter() - start)


def timed(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(stage):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def snapshot():
    with _lock:
        samples = {key: sorted(values) for key, values in _samples.items()}
    return {
        key: {"count": len(values), "p50_ms": values[math.ceil(len(values) * .50) - 1],
              "p95_ms": values[math.ceil(len(values) * .95) - 1]}
        for key, values in samples.items() if values
    }
