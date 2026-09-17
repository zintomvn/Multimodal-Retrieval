"""Request-scoped, content-free timing for synchronous retrieval and HTTP."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import logging
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class RequestTrace:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    timings: dict[str, float] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)


current_trace: ContextVar[RequestTrace | None] = ContextVar("request_trace", default=None)


@contextmanager
def stage(name: str):
    trace = current_trace.get()
    started = perf_counter()
    try:
        yield
    finally:
        if trace is not None:
            trace.timings[name] = trace.timings.get(name, 0) + (perf_counter() - started) * 1000
            trace.calls[name] = trace.calls.get(name, 0) + 1


def timed(name: str):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        trace = RequestTrace()
        token = current_trace.set(trace)
        started = perf_counter()
        status = 500

        async def send_with_timing(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                # Time to response headers, including retrieval commit/cache.
                total = (perf_counter() - started) * 1000
                timings = {**trace.timings, "request": total}
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-request-id", trace.request_id.encode()),
                    (b"server-timing", ", ".join(f"{key};dur={value:.2f}" for key, value in timings.items()).encode()),
                ]
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            # Deliberately exclude URL parameters, payloads and exception details.
            logger.info("request_completed id=%s status=%s duration_ms=%.2f stages=%s calls=%s",
                        trace.request_id, status, (perf_counter() - started) * 1000,
                        trace.timings, trace.calls)
            current_trace.reset(token)
