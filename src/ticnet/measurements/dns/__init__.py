"""DNS query timing and resolution measurement adapter package for TicNet."""

from ticnet.measurements.dns.adapter import (
    DEFAULT_DNS_TIMEOUT_S,
    MAX_DNS_TIMEOUT_S,
    MIN_DNS_TIMEOUT_S,
    DNSMeasurementAdapter,
    evaluate_dns_resolvers,
)
from ticnet.measurements.dns.models import (
    RCODE_NAMES,
    DNSMeasurementResult,
    DNSResolutionStatus,
    determine_resolution_status,
    get_rcode_name,
)
from ticnet.measurements.dns.protocol import (
    RECORD_NAME_MAP,
    RECORD_TYPE_MAP,
    DecodedDNSResponse,
    DNSMalformedPacketError,
    DNSMismatchedQuestionError,
    DNSMismatchedTransactionIDError,
    DNSValidationError,
    decode_dns_response,
    encode_dns_query,
    validate_dns_domain,
    validate_dns_qtype,
)

__all__ = [
    "DNSMeasurementAdapter",
    "DNSMeasurementResult",
    "DNSResolutionStatus",
    "determine_resolution_status",
    "evaluate_dns_resolvers",
    "get_rcode_name",
    "RCODE_NAMES",
    "encode_dns_query",
    "decode_dns_response",
    "validate_dns_domain",
    "validate_dns_qtype",
    "DecodedDNSResponse",
    "DNSValidationError",
    "DNSMalformedPacketError",
    "DNSMismatchedTransactionIDError",
    "DNSMismatchedQuestionError",
    "RECORD_TYPE_MAP",
    "RECORD_NAME_MAP",
    "DEFAULT_DNS_TIMEOUT_S",
    "MIN_DNS_TIMEOUT_S",
    "MAX_DNS_TIMEOUT_S",
]
