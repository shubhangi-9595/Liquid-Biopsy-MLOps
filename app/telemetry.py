"""Azure Application Insights via OpenTelemetry.

When APPLICATIONINSIGHTS_CONNECTION_STRING is set (Azure injects it), every request,
its latency, database calls and this service's logs are sent to Application
Insights. Locally and in tests nothing is sent. Must run before FastAPI() is created
so the FastAPI instrumentation is applied.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("liquid-biopsy-api")


def setup_telemetry() -> bool:
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn, logger_name="liquid-biopsy-api")
        log.info("Application Insights telemetry enabled")
        return True
    except Exception as exc:  # telemetry must never stop the service
        log.warning("Could not enable Application Insights: %s", exc)
        return False
