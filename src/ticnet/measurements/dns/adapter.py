"""DNS measurement adapter and resolver evaluation orchestrator.

Implements direct socket-based DNS query measurement over UDP port 53:
- Direct socket communication bypassing operating-system caching layers.
- High-resolution monotonic timing (time.perf_counter()) for query-response intervals.
- Standards-compliant RFC 1035 wire encoding and strict validation.
- Captures RCODE (NOERROR, NXDOMAIN, SERVFAIL, REFUSED, etc.) and answer section status.
- Strict non-zero null invariants: failed or timed out queries have elapsed_ms = None.
- Sockets closed on every execution path (success, timeout, exception).
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import time
from typing import List, Optional, Tuple

from ticnet.configuration.validator import DNSResolverItem, DNSResolversConfig
from ticnet.measurements.base import MeasurementAdapter
from ticnet.measurements.dns.models import (
    DNSMeasurementResult,
    DNSResolutionStatus,
    determine_resolution_status,
)
from ticnet.measurements.dns.protocol import (
    DNSMalformedPacketError,
    DNSMismatchedQuestionError,
    DNSMismatchedTransactionIDError,
    DNSValidationError,
    decode_dns_response,
    encode_dns_query,
    validate_dns_domain,
    validate_dns_qtype,
)
from ticnet.models.entities import now_utc_iso
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger, mask_public_ip_address

logger = get_logger("measurements.dns.adapter")

DEFAULT_DNS_TIMEOUT_S = 2.0
MIN_DNS_TIMEOUT_S = 0.1
MAX_DNS_TIMEOUT_S = 10.0


class DNSMeasurementAdapter(MeasurementAdapter):
    """Executes empirical DNS query measurements directly against configured resolver IPs."""

    def __init__(self, timeout_s: float = DEFAULT_DNS_TIMEOUT_S) -> None:
        super().__init__(timeout_s=timeout_s)

    @property
    def adapter_name(self) -> str:
        return "DNSMeasurementAdapter"

    @property
    def supported_platforms(self) -> Tuple[str, ...]:
        return ("win32", "linux", "darwin")

    def _create_socket(self, family: int) -> socket.socket:
        """Create a UDP socket for DNS communication (extracted for testing / dependency injection)."""
        return socket.socket(family, socket.SOCK_DGRAM)

    def execute(
        self,
        resolver_ip: str,
        domain: str,
        record_type: str = "A",
        port: int = 53,
        timeout_s: Optional[float] = None,
        resolver_id: Optional[str] = None,
    ) -> DNSMeasurementResult:
        """Execute a direct DNS query over UDP port 53 and measure elapsed response timing.
        
        Args:
            resolver_ip: Validated IPv4 or IPv6 address of target DNS resolver.
            domain: Query domain name (e.g. 'example.com').
            record_type: DNS record type mnemonic (default: 'A').
            port: UDP destination port (default: 53).
            timeout_s: Per-query timeout in seconds (default: adapter timeout).
            resolver_id: Optional identifier from configuration (e.g. 'dns-cloudflare').
            
        Returns:
            Normalized DNSMeasurementResult instance.
        """
        start_time_iso = now_utc_iso()
        resolved_timeout = timeout_s if timeout_s is not None else self.timeout_s

        # 1. Validate Resolver IP
        if not isinstance(resolver_ip, str) or not resolver_ip.strip():
            logger.error("Resolver IP is missing or empty")
            return DNSMeasurementResult(
                resolver_ip=resolver_ip,
                resolver_port=port,
                ip_family="UNKNOWN",
                query_domain=domain if isinstance(domain, str) else "",
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message="Resolver IP address cannot be empty or invalid",
            )

        scope_id = 0
        raw_ip_str = resolver_ip.strip()
        if "%" in raw_ip_str:
            base_ip, _, scope_str = raw_ip_str.partition("%")
            try:
                ip_obj = ipaddress.ip_address(base_ip)
                if ip_obj.version != 6:
                    raise ValueError("Zone identifier is only valid for IPv6 addresses")
                if scope_str.isdigit():
                    scope_id = int(scope_str)
                else:
                    if not hasattr(socket, "if_nametoindex"):
                        raise ValueError(f"Interface name scope identifiers are not supported on this platform: '{scope_str}'")
                    try:
                        scope_id = socket.if_nametoindex(scope_str)
                    except OSError as e:
                        raise ValueError(f"Unknown or invalid interface scope identifier '{scope_str}': {e}")
                target_ip_str = base_ip
                family = "IPv6"
                socket_family = socket.AF_INET6
            except ValueError as e:
                logger.error("Invalid scoped resolver IP: %s (%s)", resolver_ip, e)
                return DNSMeasurementResult(
                    resolver_ip=resolver_ip,
                    resolver_port=port,
                    ip_family="IPv6" if "%" in resolver_ip else "UNKNOWN",
                    query_domain=domain if isinstance(domain, str) else "",
                    query_record_type=record_type,
                    resolver_id=resolver_id,
                    start_time_utc=start_time_iso,
                    end_time_utc=now_utc_iso(),
                    elapsed_ms=None,
                    status=Status.FAILED,
                    resolution_status=DNSResolutionStatus.FAILED,
                    error_message=f"Invalid resolver IP address: '{resolver_ip}' ({e})",
                )
        else:
            try:
                ip_obj = ipaddress.ip_address(raw_ip_str)
                family = "IPv6" if ip_obj.version == 6 else "IPv4"
                socket_family = socket.AF_INET6 if ip_obj.version == 6 else socket.AF_INET
                target_ip_str = str(ip_obj)
            except ValueError as e:
                logger.error("Invalid resolver IP address provided: %s", resolver_ip)
                return DNSMeasurementResult(
                    resolver_ip=resolver_ip,
                    resolver_port=port,
                    ip_family="UNKNOWN",
                    query_domain=domain if isinstance(domain, str) else "",
                    query_record_type=record_type,
                    resolver_id=resolver_id,
                    start_time_utc=start_time_iso,
                    end_time_utc=now_utc_iso(),
                    elapsed_ms=None,
                    status=Status.FAILED,
                    resolution_status=DNSResolutionStatus.FAILED,
                    error_message=f"Invalid resolver IP address: '{resolver_ip}' ({e})",
                )

        # 2. Validate Domain Name
        try:
            clean_domain = validate_dns_domain(domain)
        except DNSValidationError as e:
            logger.error("Invalid DNS domain name provided: %s (%s)", domain, e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Invalid domain name: {e}",
            )

        # 3. Validate Record Type
        try:
            validate_dns_qtype(record_type)
        except DNSValidationError as e:
            logger.error("Invalid DNS record type provided: %s (%s)", record_type, e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Invalid record type: {e}",
            )

        # 4. Validate Port Bounds
        if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
            logger.error("Invalid port: %s", port)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port if isinstance(port, int) and not isinstance(port, bool) else 0,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Invalid port: {port}. Must be between 1 and 65535.",
            )

        # 5. Validate Timeout Bounds
        if (
            not isinstance(resolved_timeout, (int, float))
            or isinstance(resolved_timeout, bool)
            or resolved_timeout < MIN_DNS_TIMEOUT_S
            or resolved_timeout > MAX_DNS_TIMEOUT_S
        ):
            logger.error("Invalid timeout: %s", resolved_timeout)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=(
                    f"Timeout {resolved_timeout}s violates safety bounds "
                    f"({MIN_DNS_TIMEOUT_S}s to {MAX_DNS_TIMEOUT_S}s)"
                ),
            )

        # 6. Encode RFC 1035 Query Message
        try:
            query_bytes, tx_id = encode_dns_query(clean_domain, qtype=record_type)
        except Exception as e:
            logger.exception("Failed to encode DNS query: %s", e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"DNS query encoding error: {e}",
            )

        logger.info(
            "Dispatching DNS query: resolver=%s:%d (%s), domain=%s, type=%s, tx_id=0x%04x, timeout=%.1fs",
            mask_public_ip_address(target_ip_str),
            port,
            family,
            clean_domain,
            record_type,
            tx_id,
            resolved_timeout,
        )

        sock: Optional[socket.socket] = None
        try:
            sock = self._create_socket(socket_family)
            sock.settimeout(resolved_timeout)

            # Monotonic start time and strict bounded deadline
            start_monotonic = time.perf_counter()
            deadline = start_monotonic + resolved_timeout

            # Send DNS Query Datagram
            dest_addr = (target_ip_str, port, 0, scope_id) if socket_family == socket.AF_INET6 else (target_ip_str, port)
            sock.sendto(query_bytes, dest_addr)

            # Receive loop: handles possible unrelated datagrams or mismatched IDs until deadline
            while True:
                remaining_time = deadline - time.perf_counter()
                if remaining_time <= 0:
                    raise socket.timeout("Timed out waiting for matching DNS response")

                sock.settimeout(remaining_time)
                data, sender = sock.recvfrom(4096)
                sender_host = sender[0].partition("%")[0]
                sender_port = sender[1]

                # Check sender endpoint
                try:
                    sender_ip_obj = ipaddress.ip_address(sender_host)
                    if sender_ip_obj != ip_obj or sender_port != port:
                        logger.warning(
                            "Ignoring DNS datagram from unexpected sender %s:%d (expected %s:%d)",
                            sender_host,
                            sender_port,
                            target_ip_str,
                            port,
                        )
                        continue
                    if socket_family == socket.AF_INET6 and scope_id != 0 and len(sender) >= 4:
                        sender_scope = sender[3]
                        if sender_scope != 0 and sender_scope != scope_id:
                            logger.warning(
                                "Ignoring DNS datagram from unexpected scope %s (expected %s)",
                                sender_scope,
                                scope_id,
                            )
                            continue
                except (ValueError, IndexError):
                    continue

                # Quick transaction ID inspection: skip packets that do not match our query
                if len(data) >= 2:
                    pkt_tx_id = (data[0] << 8) | data[1]
                    if pkt_tx_id != tx_id:
                        logger.debug(
                            "Ignoring DNS datagram with mismatched transaction ID 0x%04x (expected 0x%04x)",
                            pkt_tx_id,
                            tx_id,
                        )
                        continue

                # Inspect question section via decode_dns_response
                try:
                    decoded = decode_dns_response(
                        data=data,
                        expected_tx_id=tx_id,
                        expected_domain=clean_domain,
                        expected_qtype=record_type,
                    )
                except (DNSMismatchedTransactionIDError, DNSMismatchedQuestionError) as e:
                    logger.debug("Ignoring datagram with mismatched transaction or question: %s", e)
                    continue

                # Matching response received: calculate high-resolution monotonic elapsed time
                elapsed_s = time.perf_counter() - start_monotonic
                elapsed_ms = round(max(0.0, elapsed_s * 1000.0), 3)
                end_time_iso = now_utc_iso()

                # Determine transport and resolution completion status
                status = Status.INCOMPLETE if decoded.is_truncated else Status.SUCCESS
                resolution_status = determine_resolution_status(
                    status=status,
                    rcode=decoded.rcode,
                    has_answer=decoded.has_answer,
                    is_truncated=decoded.is_truncated,
                )

                return DNSMeasurementResult(
                    resolver_ip=target_ip_str,
                    resolver_port=port,
                    ip_family=family,
                    query_domain=clean_domain,
                    query_record_type=record_type,
                    resolver_id=resolver_id,
                    start_time_utc=start_time_iso,
                    end_time_utc=end_time_iso,
                    elapsed_ms=elapsed_ms,
                    rcode=decoded.rcode,
                    rcode_name=decoded.rcode_name,
                    status=status,
                    resolution_status=resolution_status,
                    is_truncated=decoded.is_truncated,
                    has_answer=decoded.has_answer,
                    answer_count=decoded.ancount,
                    answer_ips=decoded.answer_ips,
                    error_message=f"DNS response truncated (TC=1)" if decoded.is_truncated else None,
                    transaction_id=tx_id,
                )

        except socket.timeout:
            logger.warning("DNS query timed out after %.1fs for resolver %s", resolved_timeout, target_ip_str)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                rcode=None,
                rcode_name=None,
                status=Status.TIMEOUT,
                resolution_status=DNSResolutionStatus.TIMEOUT,
                error_message=f"DNS query timed out after {resolved_timeout:.1f}s",
                transaction_id=tx_id,
            )
        except (DNSMalformedPacketError, DNSMismatchedQuestionError) as e:
            logger.warning("DNS packet validation error from resolver %s: %s", target_ip_str, e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                rcode=None,
                rcode_name=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Invalid DNS response: {e}",
                transaction_id=tx_id,
            )
        except OSError as e:
            logger.warning("Socket error communicating with resolver %s: %s", target_ip_str, e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                rcode=None,
                rcode_name=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Socket error: {e}",
                transaction_id=tx_id,
            )
        except Exception as e:
            logger.exception("Unexpected error during DNS measurement: %s", e)
            return DNSMeasurementResult(
                resolver_ip=target_ip_str,
                resolver_port=port,
                ip_family=family,
                query_domain=clean_domain,
                query_record_type=record_type,
                resolver_id=resolver_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                elapsed_ms=None,
                rcode=None,
                rcode_name=None,
                status=Status.FAILED,
                resolution_status=DNSResolutionStatus.FAILED,
                error_message=f"Execution error: {e}",
                transaction_id=tx_id,
            )
        finally:
            # Enforce socket closure on EVERY execution path
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


def evaluate_dns_resolvers(
    config: Optional[DNSResolversConfig] = None,
    gateway_ip: Optional[str] = None,
    adapter: Optional[DNSMeasurementAdapter] = None,
    timeout_s: Optional[float] = None,
) -> List[DNSMeasurementResult]:
    """Orchestrates DNS query measurements against all enabled configured resolvers and test domains.
    
    Resolves 'gateway' host to the dynamically discovered default gateway IP.
    
    Args:
        config: Validated DNSResolversConfig containing resolvers and test domains.
        gateway_ip: Optional default gateway IP discovered in Phase 3.
        adapter: Optional DNSMeasurementAdapter instance.
        timeout_s: Optional timeout override in seconds.
        
    Returns:
        List of DNSMeasurementResult records for each resolver and test domain pair.
    """
    if config is None:
        logger.warning("evaluate_dns_resolvers called with no configuration")
        return []

    dns_adapter = adapter or DNSMeasurementAdapter(
        timeout_s=timeout_s if timeout_s is not None else (config.timeout_ms / 1000.0)
    )

    results: List[DNSMeasurementResult] = []

    for resolver in config.resolvers:
        if not resolver.enabled:
            continue

        resolved_host = resolver.host.strip()
        # Handle dynamic gateway resolver resolution
        if resolved_host.lower() == "gateway":
            if not gateway_ip:
                logger.warning("Resolver '%s' requires discovered gateway IP, but gateway_ip is None", resolver.id)
                results.append(
                    DNSMeasurementResult(
                        resolver_ip="0.0.0.0",
                        resolver_port=resolver.port,
                        ip_family="UNKNOWN",
                        query_domain=config.test_domains[0] if config.test_domains else "",
                        resolver_id=resolver.id,
                        status=Status.INCOMPLETE,
                        resolution_status=DNSResolutionStatus.FAILED,
                        error_message="Gateway resolver requires discovered gateway IP, but discovery was not available",
                    )
                )
                continue
            resolved_host = gateway_ip.strip()

        # Execute queries across all configured test domains
        for domain in config.test_domains:
            res = dns_adapter.execute(
                resolver_ip=resolved_host,
                domain=domain,
                record_type="A",
                port=resolver.port,
                timeout_s=timeout_s if timeout_s is not None else (config.timeout_ms / 1000.0),
                resolver_id=resolver.id,
            )
            results.append(res)

    return results
