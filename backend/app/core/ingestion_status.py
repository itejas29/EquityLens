"""Ingestion status taxonomy.

Every data-fetch operation classifies its outcome into one of these statuses
rather than returning a bare success/failure boolean. This prevents the silent
data corruption documented in the audit: a rate-limited yfinance response was
previously indistinguishable from a genuinely missing symbol.
"""

from enum import Enum


class IngestionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ALREADY_CURRENT = "ALREADY_CURRENT"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_DATA = "INVALID_DATA"
    STALE_DATA = "STALE_DATA"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
