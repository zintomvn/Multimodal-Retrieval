"""Bound external calls without sharing SQLAlchemy sessions with workers.

Timed-out SDK work cannot be forcibly killed. Admission is capped, so lingering
calls cannot create an unbounded executor queue or unbounded threads.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import ContextVar, copy_context
from functools import wraps
from threading import BoundedSemaphore
from time import monotonic

deadline: ContextVar[float | None] = ContextVar('retrieval_deadline', default=None)
pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='retrieval-io')
slots = BoundedSemaphore(8)


def remaining(default=30.0):
    end = deadline.get()
    return max(0.0, end-monotonic()) if end is not None else default


def bounded_call(function, *args, max_seconds=10.0, **kwargs):
    wait = min(max_seconds, remaining())
    if wait <= 0 or not slots.acquire(timeout=wait):
        raise TimeoutError('Retrieval capacity or time budget exceeded')
    context = copy_context()
    try:
        future = pool.submit(context.run, function, *args, **kwargs)
    except Exception:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    try:
        return future.result(timeout=min(max_seconds, remaining()))
    except FutureTimeout as exc:
        future.cancel()
        raise TimeoutError('Source exceeded retrieval time budget') from exc


def search_budget(function):
    @wraps(function)
    def wrapped(self, request):
        token = deadline.set(monotonic() + request.options.deadline_ms/1000)
        try:
            return function(self, request)
        finally:
            deadline.reset(token)
    return wrapped
