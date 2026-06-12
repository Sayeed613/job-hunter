"""Job scraping and processing pipelines."""

from app.jobs.applier import JobApplier
from app.jobs.appliers.base import ApplierResult, ApplicationMethod, BaseApplier
from app.jobs.providers import JobProvider, RawJob, RemoteOKProvider
from app.jobs.providers_ext import (
    AshbyProvider,
    CutshortProvider,
    FounditProvider,
    GreenhouseProvider,
    GulfTalentProvider,
    InstahyreProvider,
    LeverProvider,
    NaukriProvider,
    WellfoundProvider,
)
from app.jobs.service import JobCollectionResult, JobService
from app.jobs.yc_provider import YCProvider

__all__ = [
    "ApplierResult",
    "ApplicationMethod",
    "AshbyProvider",
    "BaseApplier",
    "CutshortProvider",
    "FounditProvider",
    "GreenhouseProvider",
    "GulfTalentProvider",
    "InstahyreProvider",
    "JobApplier",
    "JobCollectionResult",
    "JobProvider",
    "JobService",
    "LeverProvider",
    "NaukriProvider",
    "RawJob",
    "RemoteOKProvider",
    "WellfoundProvider",
    "YCProvider",
]

