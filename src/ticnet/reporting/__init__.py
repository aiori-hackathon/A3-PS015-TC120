"""Technician reporting and data export serializers.

Implements Phase 9:
- ReportGenerator
- DiagnosticReportData
- PrivacyFilter
- serialize_to_json, serialize_to_csv, serialize_to_markdown
- sanitize_csv_cell
- mask_mac_address, mask_public_ip_address, mask_credentials
"""

from ticnet.reporting.privacy import (
    PrivacyFilter,
    DEFAULT_PRIVACY_FILTER,
    mask_mac_address,
    mask_public_ip_address,
    mask_credentials,
    is_local_address,
)
from ticnet.reporting.serializers import (
    DiagnosticReportData,
    serialize_to_json,
    serialize_to_csv,
    serialize_summaries_to_csv,
    serialize_to_markdown,
    sanitize_csv_cell,
    CSV_OBSERVATION_COLUMNS,
    CSV_SUMMARY_COLUMNS,
)
from ticnet.reporting.generator import (
    ReportGenerator,
    ReportExportError,
    redact_filesystem_path,
)

__all__ = [
    "PrivacyFilter",
    "DEFAULT_PRIVACY_FILTER",
    "mask_mac_address",
    "mask_public_ip_address",
    "mask_credentials",
    "is_local_address",
    "DiagnosticReportData",
    "serialize_to_json",
    "serialize_to_csv",
    "serialize_summaries_to_csv",
    "serialize_to_markdown",
    "sanitize_csv_cell",
    "CSV_OBSERVATION_COLUMNS",
    "CSV_SUMMARY_COLUMNS",
    "ReportGenerator",
    "ReportExportError",
    "redact_filesystem_path",
]
