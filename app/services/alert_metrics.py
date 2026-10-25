"""Per-call counters, persisted by the fenced morning coordinator."""
from contextvars import ContextVar

current = ContextVar("alert_metrics", default=None)


def record(name):
    counters = current.get()
    if counters is not None:
        counters[name] = counters.get(name, 0) + 1
