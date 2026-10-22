"""Process-wide serialization for transactions that use the physical Rev-o-mate."""
from contextlib import contextmanager
from functools import wraps
import threading

DEVICE_LOCK = threading.RLock()
_state_lock = threading.Lock()
_busy_depth = 0
_close_callbacks = []


def register_close(callback):
    """Register an idempotent callback that closes an LED/device handle."""
    if not callable(callback):
        raise TypeError("callback must be callable")
    with _state_lock:
        if callback not in _close_callbacks:
            _close_callbacks.append(callback)


def device_busy():
    """Return whether a physical-device transaction is in progress."""
    with _state_lock:
        return _busy_depth > 0


@contextmanager
def device_operation():
    """Serialize a complete physical operation and keep LED handles closed throughout."""
    global _busy_depth
    DEVICE_LOCK.acquire()
    try:
        with _state_lock:
            _busy_depth += 1
            callbacks = tuple(_close_callbacks)
        try:
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    pass
            yield
        finally:
            with _state_lock:
                _busy_depth -= 1
    finally:
        DEVICE_LOCK.release()


def serialized_device_operation(function):
    """Decorate a backend function that performs a direct device operation."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with device_operation():
            return function(*args, **kwargs)
    return wrapped
