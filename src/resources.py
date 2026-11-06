"""Configurable service budgets and killable, bounded computation admission."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import multiprocessing as mp
import os
from threading import Condition, Event, Thread
import time

import psutil
from pydantic import BaseModel, ConfigDict, Field


class ResourceLimit(ValueError):
    pass


class QueueFull(ResourceLimit):
    pass


class ComputationTimeout(ResourceLimit):
    pass


class ResourceProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    upload_bytes: int = Field(default=20 * 1024**2, gt=0)
    data_rows: int = Field(default=100_000, gt=0)
    data_columns: int = Field(default=100, gt=0)
    result_rows: int = Field(default=1000, gt=0)
    response_bytes: int = Field(default=2 * 1024**2, gt=0)
    xlsx_expanded_bytes: int = Field(default=100 * 1024**2, gt=0)
    xlsx_entries: int = Field(default=2048, gt=0)
    session_storage_bytes: int = Field(default=100 * 1024**2, gt=0)
    total_storage_bytes: int = Field(default=500 * 1024**2, gt=0)
    session_memory_bytes: int = Field(default=512 * 1024**2, gt=0)
    total_memory_bytes: int = Field(default=1024 * 1024**2, gt=0)
    waiting_jobs: int = Field(default=4, ge=0)
    prepare_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    query_seconds: float = Field(default=15.0, gt=0, allow_inf_nan=False)
    render_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    render_figures: int = Field(default=8, gt=0)
    queue_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    dataset_ttl: int = Field(default=3600, gt=0)


_execution = ContextVar('resource_execution', default=None)


def execute(function, *args, **kwargs):
    context = _execution.get()
    return function(*args, **kwargs) if context is None else context[0].run(function, args, kwargs, *context[1:])


def check_response(value, profile):
    value = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    def count(node):
        if isinstance(node, dict):
            return sum((len(v) if k == 'records' and isinstance(v, list) else count(v)) for k, v in node.items())
        if isinstance(node, list):
            return sum(count(v) for v in node)
        return 0
    if count(value) > profile.result_rows:
        raise ResourceLimit('Result row limit exceeded; narrow the query.')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) > profile.response_bytes:
        raise ResourceLimit('Response byte limit exceeded; narrow the query.')


def _child(connection, function, args, kwargs, profile):
    try:
        result = function(*args, **kwargs)
        # Reject large analytical JSON before it crosses IPC into the API process.
        if isinstance(result, dict):
            check_response({k: v for k, v in result.items() if k != 'frame'}, profile)
        connection.send((True, result))
    except Exception as exc:
        connection.send((False, exc))
    finally:
        connection.close()


class BoundedWorker:
    """One admitted request, bounded FIFO waiting; each task gets an isolated process.

    No process pool future is abandoned: timeout/over-budget children are killed
    and joined before admission is released. Spawn also works on Windows.
    """
    def __init__(self, profile):
        self.profile = profile
        self._condition = Condition()
        self._busy = False
        self._waiting = []
        self._closed = False
        self.last_pid = None

    @contextmanager
    def admission(self, seconds, session_memory):
        ticket = object()
        with self._condition:
            if self._closed:
                raise QueueFull('Computation service is closed.')
            if self._busy or self._waiting:
                if len(self._waiting) >= self.profile.waiting_jobs:
                    raise QueueFull('Computation queue is full.')
                self._waiting.append(ticket)
                until = time.monotonic() + self.profile.queue_seconds
                try:
                    while self._busy or self._waiting[0] is not ticket:
                        remaining = until - time.monotonic()
                        if remaining <= 0:
                            raise QueueFull('Computation queue wait expired.')
                        self._condition.wait(remaining)
                        if self._closed:
                            raise QueueFull('Computation service is closed.')
                finally:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()
            self._busy = True
        token = _execution.set((self, time.monotonic() + seconds, session_memory))
        try:
            yield
        finally:
            _execution.reset(token)
            with self._condition:
                self._busy = False
                self._condition.notify_all()

    def run(self, function, args, kwargs, deadline, session_memory):
        retained_memory = session_memory()
        context = mp.get_context('spawn')
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=_child, args=(writer, function, args, kwargs, self.profile), daemon=True)
        received, outcome = Event(), []
        receiver = None
        def receive():
            try:
                outcome.append(reader.recv())
            except (EOFError, OSError):
                outcome.append((False, ResourceLimit('Worker exited without a result.')))
            finally:
                received.set()
        try:
            process.start()
            writer.close()
            self.last_pid = process.pid
            child = psutil.Process(process.pid)
            parent = psutil.Process(os.getpid())
            receiver = Thread(target=receive, daemon=True)
            receiver.start()
            while True:
                if time.monotonic() >= deadline:
                    raise ComputationTimeout('Computation exceeded its time budget and was stopped.')
                try:
                    rss = child.memory_info().rss
                    if (rss + retained_memory > self.profile.session_memory_bytes or
                            rss + parent.memory_info().rss > self.profile.total_memory_bytes):
                        raise ResourceLimit('Computation memory budget exceeded.')
                except psutil.NoSuchProcess:
                    pass
                if received.wait(.01):
                    if time.monotonic() >= deadline:
                        raise ComputationTimeout('Computation exceeded its time budget and was stopped.')
                    if parent.memory_info().rss > self.profile.total_memory_bytes:
                        raise ResourceLimit('Computation memory budget exceeded.')
                    ok, result = outcome[0]
                    if not ok:
                        raise result
                    return result
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join()
                process.close()
            if receiver is not None:
                receiver.join(timeout=2)
            writer.close()
            reader.close()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
