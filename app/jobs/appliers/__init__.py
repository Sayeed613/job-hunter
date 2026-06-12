"""Application submission handlers for various job platforms."""

from app.jobs.appliers.base import (
    ApplierResult,
    BaseApplier,
    ApplicationMethod,
)
from app.jobs.appliers.greenhouse import GreenhouseApplier
from app.jobs.appliers.lever import LeverApplier
from app.jobs.appliers.ashby import AshbyApplier
from app.jobs.appliers.email_applier import EmailApplier
from app.jobs.appliers.web_applier import WebApplier

__all__ = [
    "ApplierResult",
    "ApplicationMethod",
    "AshbyApplier",
    "BaseApplier",
    "EmailApplier",
    "GreenhouseApplier",
    "LeverApplier",
    "WebApplier",
]
