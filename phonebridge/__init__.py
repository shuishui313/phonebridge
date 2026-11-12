"""PhoneBridge — independent MCP interface for simulated iPhone automation.

Imports of the core and drivers are lazy. ``python -m phonebridge.remote_readonly``
must not load write, approval, or device-driver modules as a side effect.
"""

__version__ = "0.4.0"
__all__ = ["PhoneBridge", "MockIPhone", "AppleSimulatorDriver"]


def __getattr__(name: str):
    if name == "PhoneBridge":
        from .core import PhoneBridge
        globals()["PhoneBridge"] = PhoneBridge
        return PhoneBridge
    if name in {"MockIPhone", "AppleSimulatorDriver"}:
        from .drivers import AppleSimulatorDriver, MockIPhone
        globals()["MockIPhone"] = MockIPhone
        globals()["AppleSimulatorDriver"] = AppleSimulatorDriver
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
