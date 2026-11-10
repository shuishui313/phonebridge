"""PhoneBridge — independent MCP interface for simulated iPhone automation."""
from .core import PhoneBridge
from .drivers import MockIPhone, AppleSimulatorDriver

__version__ = "0.4.0"
__all__ = ["PhoneBridge", "MockIPhone", "AppleSimulatorDriver"]
