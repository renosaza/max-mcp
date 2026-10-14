"""Shared PyMax configuration for login and MCP runtime."""

import os
from typing import Any

from pymax import ExtraConfig
from pymax.versions import VersionCatalog

DEFAULT_APP_VERSION = "26.35.1"


def secure_client_config() -> ExtraConfig:
    # PyMax debug frames may include credentials or message contents.
    return ExtraConfig(log_level="CRITICAL", telemetry=False)


def mobile_client_options() -> dict[str, Any]:
    version = os.environ.get("MAX_MCP_APP_VERSION") or DEFAULT_APP_VERSION
    catalog = VersionCatalog()
    if version not in catalog.versions:
        catalog = VersionCatalog(remote=True)
    return {"app_version": version, "catalog": catalog}
