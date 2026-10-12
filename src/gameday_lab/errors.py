"""Part A1 — Error classification.

The single most important decision in a reliability layer is *what kind of
failure* you are looking at, because each kind needs a different reaction.

Tests: tests/test_errors_retry.py
"""

from __future__ import annotations

from enum import Enum

from gameday_lab.sim.contracts import ProviderError


class ErrorKind(str, Enum):
    RATE_LIMITED = "rate_limited"  # 429 + retry-after: our tenant is over its limit
    QUOTA_EXHAUSTED = "quota_exhausted"  # 429 insufficient_quota: no point retrying
    OVERLOADED = "overloaded"  # 529 / 503: provider is shedding load or down
    SERVER_ERROR = "server_error"  # 500 / 502 / 504
    TIMEOUT = "timeout"  # we gave up waiting (status 0, code client_timeout)
    CLIENT_ERROR = "client_error"  # 400 / 401 / 403 / 404 / 422: our bug or config


def classify(err: ProviderError) -> ErrorKind:
    """Map a provider error to an ErrorKind.

    TODO(student). Rules:
    * status 0 (client gave up) or 408                  -> TIMEOUT
    * 429 with code "insufficient_quota"                -> QUOTA_EXHAUSTED
    * any other 429                                     -> RATE_LIMITED
    * 503, 529                                          -> OVERLOADED
    * other 5xx                                         -> SERVER_ERROR
    * everything else (4xx)                             -> CLIENT_ERROR
    Look at ``err.status`` and ``err.code``.
    """
    raise NotImplementedError("TODO(student): classify()")


def is_retryable(kind: ErrorKind) -> bool:
    """May we try *the same provider* again for this call (after a delay)?

    TODO(student). Retryable: RATE_LIMITED (after retry-after), OVERLOADED,
    SERVER_ERROR, TIMEOUT. Not retryable: QUOTA_EXHAUSTED, CLIENT_ERROR.
    """
    raise NotImplementedError("TODO(student): is_retryable()")


def counts_toward_breaker(kind: ErrorKind) -> bool:
    """Does this error say something about the *health* of the provider?

    TODO(student).
    Yes: OVERLOADED, SERVER_ERROR, TIMEOUT.
    No:  RATE_LIMITED (our quota, handled by a cooldown), QUOTA_EXHAUSTED (billing,
         handled by a cooldown + alert), CLIENT_ERROR (our bug — every provider
         would reject it, so it must never open a breaker).
    """
    raise NotImplementedError("TODO(student): counts_toward_breaker()")


def failover_helps(kind: ErrorKind) -> bool:
    """Would sending the same request to *another* provider help?

    TODO(student). Everything except CLIENT_ERROR: a malformed request is
    malformed everywhere.
    """
    raise NotImplementedError("TODO(student): failover_helps()")
