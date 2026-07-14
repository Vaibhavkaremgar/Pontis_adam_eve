from __future__ import annotations


class BrowserError(Exception):
    pass


class BrowserLaunchError(BrowserError):
    pass


class BrowserClosedError(BrowserError):
    pass


class SessionExpiredError(BrowserError):
    pass


class PersistentProfileError(BrowserError):
    pass


class ConfigurationError(BrowserError):
    pass
