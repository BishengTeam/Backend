"""One-time, loopback-only deployment bootstrap service.

This package intentionally does not import :mod:`app.port.config`.  The
production application settings require secrets that do not exist until the
bootstrap transaction has completed.
"""

__all__ = ["__version__"]

__version__ = "1"
