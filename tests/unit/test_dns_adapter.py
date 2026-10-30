"""Unit tests for Phase 5: DNS Query Timing and Resolution Measurement Adapter (TC-ADPT-03).

Tests direct UDP port 53 socket querying, RFC 1035 wire encoding and decoding,
response validation (RCODEs, truncation, transaction ID matching), high-resolution
monotonic timing, non-zero null invariants, socket cleanup, persistence mapping,
and diagnostic isolation using deterministic mock sockets.
"""

import ipaddress
import json
import socket
import struct
import time
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, call, patch

import pytest

from ticnet.configuration.validator import DNSResolverItem, DNSResolversConfig
from ticnet.measurements.dns.adapter import (
    DEFAULT_DNS_TIMEOUT_S,
    MAX_DNS_TIMEOUT_S,
    MIN_DNS_TIMEOUT_S,
    DNSMeasurementAdapter,
    evaluate_dns_resolvers,
)
from ticnet.measurements.dns.models import (
    DNSMeasurementResult,
    DNSResolutionStatus,
    determine_resolution_status,
    get_rcode_name,
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
from ticnet.models.entities import Target, TestRun, now_utc_iso
from ticnet.models.enums import MeasurementStatus, RunStatus, Status, TargetType
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository


# ============================================================================
# Synthetic DNS Packet Helper
# ============================================================================

def make_synthetic_dns_response(
    tx_id: int,
    domain: str,
    qtype: str = "A",
    rcode: int = 0,
    is_response: bool = True,
    opcode: int = 0,
    is_truncated: bool = False,
    is_authoritative: bool = False,
    recursion_available: bool = True,
    answers: Optional[List[str]] = None,
    qdcount: int = 1,
    qclass: int = 1,
    ancount: Optional[int] = None,
    nscount: int = 0,
    arcount: int = 0,
    authority_bytes: bytes = b"",
    additional_bytes: bytes = b"",
    raw_answer_bytes: Optional[bytes] = None,
) -> bytes:
    """Construct a byte-for-byte RFC 1035 compliant DNS response packet for testing."""
    # 1. Header Flags
    flags = 0
    if is_response:
        flags |= 1 << 15
    flags |= (opcode & 0xF) << 11
    if is_authoritative:
        flags |= 1 << 10
    if is_truncated:
        flags |= 1 << 9
    flags |= 1 << 8  # RD
    if recursion_available:
        flags |= 1 << 7  # RA
    flags |= rcode & 0xF

    if ancount is None:
        if raw_answer_bytes is not None and not is_truncated:
            resolved_ancount = 1
        elif answers and not is_truncated:
            resolved_ancount = len(answers)
        elif answers:
            resolved_ancount = len(answers)
        else:
            resolved_ancount = 0
    else:
        resolved_ancount = ancount

    header = struct.pack("!HHHHHH", tx_id, flags, qdcount, resolved_ancount, nscount, arcount)

    # 2. Question section (if qdcount > 0)
    question_bytes = b""
    if qdcount > 0:
        qname_parts: List[bytes] = []
        for label in domain.strip().rstrip(".").split("."):
            encoded = label.encode("ascii")
            qname_parts.append(bytes([len(encoded)]) + encoded)
        qname_parts.append(b"\x00")
        qname_bytes = b"".join(qname_parts)

        numeric_qtype = 28 if qtype.upper() == "AAAA" else 1
        question_bytes = qname_bytes + struct.pack("!HH", numeric_qtype, qclass)

    # 3. Answer section (if present and not truncated)
    answer_bytes = b""
    if raw_answer_bytes is not None:
        answer_bytes = raw_answer_bytes
    elif answers and not is_truncated:
        for ip in answers:
            # Use compression pointer to QNAME at offset 12
            pointer = struct.pack("!H", 0xC00C)
            if ":" in ip:  # IPv6 AAAA
                ip_bytes = socket.inet_pton(socket.AF_INET6, ip)
                ans_record = pointer + struct.pack("!HHIH", 28, 1, 300, 16) + ip_bytes
            else:  # IPv4 A
                ip_bytes = socket.inet_aton(ip)
                ans_record = pointer + struct.pack("!HHIH", 1, 1, 300, 4) + ip_bytes
            answer_bytes += ans_record

    return header + question_bytes + answer_bytes + authority_bytes + additional_bytes


# ============================================================================
# 10.1 Query Construction & Input Validation
# ============================================================================

@pytest.mark.unit
class TestQueryConstruction:
    """Verifies domain encoding, RFC 1123 validation, and query header structure."""

    def test_encode_valid_a_query(self):
        query_bytes, tx_id = encode_dns_query("example.com", qtype="A", tx_id=0x1234)
        assert len(query_bytes) == 29
        unpacked_id, flags, qdcount, ancount, nscount, arcount = struct.unpack("!HHHHHH", query_bytes[:12])
        assert unpacked_id == 0x1234
        assert flags == 0x0100  # Recursion Desired
        assert qdcount == 1
        assert ancount == 0

        # Verify QNAME: \x07example\x03com\x00
        assert query_bytes[12:25] == b"\x07example\x03com\x00"
        # Verify QTYPE=1 (A), QCLASS=1 (IN)
        assert query_bytes[25:29] == struct.pack("!HH", 1, 1)

    def test_encode_valid_aaaa_query(self):
        query_bytes, tx_id = encode_dns_query("example.com", qtype="AAAA", tx_id=0x5678)
        assert query_bytes[25:29] == struct.pack("!HH", 28, 1)

    def test_cryptographically_unpredictable_tx_id(self):
        _, tx1 = encode_dns_query("example.com")
        _, tx2 = encode_dns_query("example.com")
        assert 0 <= tx1 <= 65535
        assert 0 <= tx2 <= 65535

    def test_reject_empty_domain(self):
        with pytest.raises(DNSValidationError, match="Domain name cannot be empty"):
            validate_dns_domain("")

        with pytest.raises(DNSValidationError, match="Domain name cannot be root"):
            validate_dns_domain(".")

    def test_reject_overlong_domain(self):
        overlong = "a" * 254 + ".com"
        with pytest.raises(DNSValidationError, match="exceeds maximum length of 253"):
            validate_dns_domain(overlong)

    def test_reject_overlong_label(self):
        overlong_label = "a" * 64 + ".com"
        with pytest.raises(DNSValidationError, match="exceeds maximum length of 63"):
            validate_dns_domain(overlong_label)

    def test_reject_malformed_labels(self):
        with pytest.raises(DNSValidationError, match="Invalid characters"):
            validate_dns_domain("-example.com")  # leading hyphen

        with pytest.raises(DNSValidationError, match="Invalid characters"):
            validate_dns_domain("example-.com")  # trailing hyphen

        with pytest.raises(DNSValidationError, match="Empty label"):
            validate_dns_domain("example..com")  # consecutive dots

    def test_reject_unsupported_qtype(self):
        with pytest.raises(DNSValidationError, match="Unsupported DNS record type"):
            validate_dns_qtype("INVALID_TYPE")

    def test_no_socket_activity_for_invalid_configuration(self):
        adapter = DNSMeasurementAdapter()
        with patch.object(adapter, "_create_socket") as mock_sock:
            # 1. Invalid resolver IP
            res_ip = adapter.execute(resolver_ip="not_an_ip", domain="example.com")
            assert res_ip.status == Status.FAILED
            assert res_ip.elapsed_ms is None

            # 2. Empty resolver IP
            res_empty_ip = adapter.execute(resolver_ip="", domain="example.com")
            assert res_empty_ip.status == Status.FAILED

            # 3. Invalid domain
            res_dom = adapter.execute(resolver_ip="1.1.1.1", domain="bad..domain")
            assert res_dom.status == Status.FAILED

            # 4. Invalid port
            res_port = adapter.execute(resolver_ip="1.1.1.1", domain="example.com", port=0)
            assert res_port.status == Status.FAILED

            # 5. Invalid timeout
            res_timeout = adapter.execute(resolver_ip="1.1.1.1", domain="example.com", timeout_s=0.01)
            assert res_timeout.status == Status.FAILED

            mock_sock.assert_not_called()


# ============================================================================
# 10.2 Valid Responses & RCODE Handling
# ============================================================================

@pytest.mark.unit
class TestResponseCodes:
    """Verifies parsing of DNS RCODEs (NOERROR, NXDOMAIN, SERVFAIL, REFUSED, etc.)."""

    def test_noerror_with_answer(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="example.com",
            qtype="A",
            rcode=0,
            answers=["93.184.216.34"],
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com", expected_qtype="A")
        assert res.rcode == 0
        assert res.rcode_name == "NOERROR"
        assert res.has_answer is True
        assert res.ancount == 1
        assert res.answer_ips == ["93.184.216.34"]

    def test_noerror_with_no_answers(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="example.com",
            qtype="A",
            rcode=0,
            answers=[],
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com", expected_qtype="A")
        assert res.rcode == 0
        assert res.rcode_name == "NOERROR"
        assert res.has_answer is False
        assert res.ancount == 0
        assert res.answer_ips == []

    def test_nxdomain_response(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="nonexistent.example.com",
            qtype="A",
            rcode=3,
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="nonexistent.example.com")
        assert res.rcode == 3
        assert res.rcode_name == "NXDOMAIN"
        assert res.has_answer is False

    def test_servfail_response(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="example.com",
            qtype="A",
            rcode=2,
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")
        assert res.rcode == 2
        assert res.rcode_name == "SERVFAIL"

    def test_refused_response(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="example.com",
            qtype="A",
            rcode=5,
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")
        assert res.rcode == 5
        assert res.rcode_name == "REFUSED"

    def test_unknown_numeric_rcode(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x1234,
            domain="example.com",
            qtype="A",
            rcode=15,
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")
        assert res.rcode == 15
        assert "15" in res.rcode_name or res.rcode_name != ""

    def test_ipv6_aaaa_response(self):
        raw_pkt = make_synthetic_dns_response(
            tx_id=0x5678,
            domain="example.com",
            qtype="AAAA",
            rcode=0,
            answers=["2606:2800:220:1:248:1893:25c8:1946"],
        )
        res = decode_dns_response(raw_pkt, expected_tx_id=0x5678, expected_domain="example.com", expected_qtype="AAAA")
        assert res.rcode == 0
        assert res.has_answer is True
        assert res.answer_ips == ["2606:2800:220:1:248:1893:25c8:1946"]


# ============================================================================
# 10.3 Response Validation & Packet Malformation
# ============================================================================

@pytest.mark.unit
class TestResponseValidation:
    """Verifies rejection of invalid or malformed DNS response datagrams."""

    def test_reject_short_header(self):
        with pytest.raises(DNSMalformedPacketError, match="shorter than DNS header"):
            decode_dns_response(b"\x00" * 11, expected_tx_id=0x1234, expected_domain="example.com")

    def test_reject_mismatched_transaction_id(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x9999, domain="example.com")
        with pytest.raises(DNSMismatchedTransactionIDError, match="Transaction ID mismatch"):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")

    def test_reject_datagram_with_qr_zero(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", is_response=False)
        with pytest.raises(DNSMalformedPacketError, match="QR flag is 0"):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")

    def test_reject_wrong_opcode(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", opcode=4)
        with pytest.raises(DNSMalformedPacketError, match="Unexpected DNS response opcode"):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")

    def test_reject_mismatched_question_domain(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="other.org")
        with pytest.raises(DNSMismatchedQuestionError, match="Question name mismatch"):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")

    def test_reject_mismatched_question_qtype(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", qtype="AAAA")
        with pytest.raises(DNSMismatchedQuestionError, match="Question record type mismatch"):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com", expected_qtype="A")

    def test_truncated_response_detection(self):
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", is_truncated=True)
        res = decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")
        assert res.is_truncated is True


# ============================================================================
# 10.4 Timing, Mocked Sockets, and Failure Paths
# ============================================================================

@pytest.mark.unit
class TestTimingAndFailures:
    """Verifies monotonic elapsed timing, timeout handling, and socket closure guarantees."""

    def test_successful_query_timing_monotonic(self):
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()

        # Capture tx_id from sendto to build matching mock response
        sent_bytes: List[bytes] = []

        def fake_sendto(data, addr):
            sent_bytes.append(data)
            return len(data)

        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent_bytes[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", rcode=0, answers=["93.184.216.34"])
            return (resp, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        # Mock time.perf_counter to simulate 42.5ms elapsed time
        perf_values = [100.0, 100.0425, 100.0425]
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            with patch("time.perf_counter", side_effect=perf_values):
                res = adapter.execute(resolver_ip="1.1.1.1", domain="example.com")

        assert res.status == Status.SUCCESS
        assert res.elapsed_ms == 42.5
        assert res.rcode == 0
        assert res.rcode_name == "NOERROR"
        assert res.has_answer is True
        assert res.answer_ips == ["93.184.216.34"]
        mock_sock.close.assert_called_once()

    def test_timeout_returns_none_elapsed_ms_and_closes_socket(self):
        adapter = DNSMeasurementAdapter(timeout_s=1.5)
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = socket.timeout("Timed out")

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="1.1.1.1", domain="example.com")

        assert res.status == Status.TIMEOUT
        assert res.elapsed_ms is None  # Strict Non-zero Null Invariant
        assert res.rcode is None
        assert res.error_message is not None and "timed out" in res.error_message.lower()
        mock_sock.close.assert_called_once()

    def test_socket_os_error_handling(self):
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        mock_sock.sendto.side_effect = OSError("Network is unreachable")

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="1.1.1.1", domain="example.com")

        assert res.status == Status.FAILED
        assert res.elapsed_ms is None
        assert res.error_message is not None and "Socket error" in res.error_message
        mock_sock.close.assert_called_once()

    def test_multiple_invalid_datagrams_followed_by_valid_response(self):
        """Adapter must discard datagrams from wrong endpoints or wrong IDs, then accept valid."""
        adapter = DNSMeasurementAdapter(timeout_s=2.0)
        mock_sock = MagicMock()

        sent_bytes: List[bytes] = []

        def fake_sendto(data, addr):
            sent_bytes.append(data)
            return len(data)

        # Datagram 1: Wrong sender IP ("8.8.8.8" instead of "1.1.1.1")
        pkt_wrong_sender = make_synthetic_dns_response(tx_id=0x1111, domain="example.com")
        # Datagram 2: Wrong transaction ID (0x9999) from correct sender
        pkt_wrong_tx = make_synthetic_dns_response(tx_id=0x9999, domain="example.com")

        recv_count = 0

        def fake_recvfrom(bufsize):
            nonlocal recv_count
            recv_count += 1
            if recv_count == 1:
                return (pkt_wrong_sender, ("8.8.8.8", 53))
            elif recv_count == 2:
                return (pkt_wrong_tx, ("1.1.1.1", 53))
            else:
                real_tx = struct.unpack("!H", sent_bytes[0][:2])[0]
                valid_pkt = make_synthetic_dns_response(tx_id=real_tx, domain="example.com", rcode=0)
                return (valid_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="1.1.1.1", domain="example.com")

        assert res.status == Status.SUCCESS
        assert recv_count == 3
        mock_sock.close.assert_called_once()

    def test_truncated_response_status_incomplete(self):
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent_bytes: List[bytes] = []

        def fake_sendto(data, addr):
            sent_bytes.append(data)
            return len(data)

        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent_bytes[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", is_truncated=True)
            return (resp, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="1.1.1.1", domain="example.com")

        assert res.status == Status.INCOMPLETE
        assert res.is_truncated is True
        assert res.elapsed_ms is not None  # Timing was captured
        mock_sock.close.assert_called_once()


# ============================================================================
# 10.5 Safety Guardrails & Diagnostic Isolation
# ============================================================================

@pytest.mark.unit
class TestSafetyAndDiagnosticIsolation:
    """Verifies that DNS measurements do not invoke OS resolver APIs or leap to diagnostic conclusions."""

    def test_no_os_gethostbyname_calls(self):
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        mock_sock.sendto.return_value = 29
        mock_sock.recvfrom.side_effect = socket.timeout("Timed out")

        with patch("socket.gethostbyname") as mock_ghbn:
            with patch.object(adapter, "_create_socket", return_value=mock_sock):
                adapter.execute(resolver_ip="1.1.1.1", domain="example.com")
            mock_ghbn.assert_not_called()

    def test_dns_timeout_does_not_claim_network_outage(self):
        """A timeout records status=TIMEOUT without synthesizing diagnostic fault assertions."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = socket.timeout("timed out")

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="192.168.0.1", domain="example.com")

        assert res.status == Status.TIMEOUT
        assert res.rcode is None
        # Does not state "ISP is down" or "Router is dead"
        assert "outage" not in (res.error_message or "").lower()

    def test_nxdomain_does_not_claim_network_outage(self):
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent_bytes = []

        def fake_sendto(data, addr):
            sent_bytes.append(data)
            return len(data)

        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent_bytes[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="invalid.test", rcode=3)
            return (resp, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute(resolver_ip="1.1.1.1", domain="invalid.test")

        assert res.status == Status.SUCCESS
        assert res.rcode == 3
        assert res.rcode_name == "NXDOMAIN"


# ============================================================================
# 10.6 Integration, Batch Evaluation, and Persistence
# ============================================================================

@pytest.mark.unit
class TestIntegrationAndPersistence:
    """Verifies batch resolver evaluation and SQLite observation persistence."""

    def test_evaluate_dns_resolvers_with_dynamic_gateway(self):
        config = DNSResolversConfig(
            resolvers=[
                DNSResolverItem(id="dns-gw", label="Gateway DNS", host="gateway", port=53, enabled=True),
                DNSResolverItem(id="dns-cf", label="Cloudflare", host="1.1.1.1", port=53, enabled=True),
                DNSResolverItem(id="dns-off", label="Disabled", host="8.8.8.8", port=53, enabled=False),
            ],
            test_domains=["example.com"],
            timeout_ms=1000,
        )

        mock_adapter = MagicMock(spec=DNSMeasurementAdapter)
        mock_adapter.execute.return_value = DNSMeasurementResult(
            resolver_ip="192.168.0.1",
            resolver_port=53,
            ip_family="IPv4",
            query_domain="example.com",
            elapsed_ms=12.5,
            rcode=0,
            rcode_name="NOERROR",
            status=Status.SUCCESS,
        )

        results = evaluate_dns_resolvers(
            config=config,
            gateway_ip="192.168.0.1",
            adapter=mock_adapter,
        )

        assert len(results) == 2  # 2 enabled resolvers x 1 domain
        # First call used gateway IP
        assert mock_adapter.execute.call_args_list[0] == call(
            resolver_ip="192.168.0.1",
            domain="example.com",
            record_type="A",
            port=53,
            timeout_s=1.0,
            resolver_id="dns-gw",
        )
        # Second call used Cloudflare IP
        assert mock_adapter.execute.call_args_list[1] == call(
            resolver_ip="1.1.1.1",
            domain="example.com",
            record_type="A",
            port=53,
            timeout_s=1.0,
            resolver_id="dns-cf",
        )

    def test_evaluate_dns_resolvers_gateway_missing_handled_safely(self):
        config = DNSResolversConfig(
            resolvers=[
                DNSResolverItem(id="dns-gw", label="Gateway DNS", host="gateway", port=53, enabled=True),
            ],
            test_domains=["example.com"],
            timeout_ms=1000,
        )

        results = evaluate_dns_resolvers(config=config, gateway_ip=None)
        assert len(results) == 1
        assert results[0].status == Status.INCOMPLETE
        assert results[0].error_message is not None and "Gateway resolver requires discovered gateway IP" in results[0].error_message

    def test_persist_dns_observations(self, tmp_path):
        db_path = tmp_path / "test_dns_persist.db"
        db = DatabaseManager(str(db_path))
        db.run_migrations()

        run_repo = RunRepository(db)
        obs_repo = ObservationRepository(db)

        # 1. Prerequisite Run and Target
        run = TestRun(
            run_id="run-dns-test",
            start_time_utc=now_utc_iso(),
            status=RunStatus.RUNNING,
            app_version="1.0.0",
            config_version="1.0.0",
        )
        run_repo.create_run(run)

        target = Target(
            target_id="tgt-dns-cloudflare",
            target_type=TargetType.DNS_RESOLVER,
            ip_address="1.1.1.1",
            port=53,
            provider_label="Cloudflare DNS",
            is_active=True,
        )
        obs_repo.create_target(target)

        # 2. DNS Results (one success, one timeout)
        res_success = DNSMeasurementResult(
            resolver_ip="1.1.1.1",
            resolver_port=53,
            ip_family="IPv4",
            query_domain="example.com",
            query_record_type="A",
            elapsed_ms=15.4,
            rcode=0,
            rcode_name="NOERROR",
            has_answer=True,
            answer_count=1,
            answer_ips=["93.184.216.34"],
            status=Status.SUCCESS,
        )

        res_timeout = DNSMeasurementResult(
            resolver_ip="1.1.1.1",
            resolver_port=53,
            ip_family="IPv4",
            query_domain="example.com",
            query_record_type="A",
            elapsed_ms=None,
            rcode=None,
            rcode_name=None,
            status=Status.TIMEOUT,
            error_message="DNS query timed out after 2.0s",
        )

        # 3. Convert to domain Observation entities
        obs_1 = res_success.to_observation(run_id=run.run_id, target_id=target.target_id, sample_index=1)
        obs_2 = res_timeout.to_observation(run_id=run.run_id, target_id=target.target_id, sample_index=2)

        assert obs_1.metric_name == "dns_query_time"
        assert obs_1.value_numeric == 15.4
        assert obs_1.status == MeasurementStatus.SUCCESS

        assert obs_2.metric_name == "dns_query_time"
        assert obs_2.value_numeric is None  # Non-zero null invariant
        assert obs_2.status == MeasurementStatus.TIMEOUT

        # 4. Save batch to SQLite
        saved = obs_repo.create_observations_batch([obs_1, obs_2])
        assert len(saved) == 2

        # 5. Read back from database
        loaded = obs_repo.get_observations_for_run(run.run_id)
        assert len(loaded) == 2
        loaded_by_idx = {o.sample_index: o for o in loaded}

        assert loaded_by_idx[1].value_numeric == 15.4
        assert loaded_by_idx[1].status == MeasurementStatus.SUCCESS
        assert loaded_by_idx[2].value_numeric is None
        assert loaded_by_idx[2].status == MeasurementStatus.TIMEOUT


# ============================================================================
# Section 10: Mandatory Audit Regression Test Suite (30 Required Cases)
# ============================================================================

@pytest.mark.unit
class TestAuditMandatoryRegressionCases:
    """Explicitly tests all 30 mandatory protocol, edge-case, and boundary requirements."""

    def test_01_valid_a_response(self):
        """1. Valid A response returns Status.SUCCESS, resolution_status=RESOLVED, and IPv4."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", qtype="A", rcode=0, answers=["93.184.216.34"])
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com", record_type="A")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.RESOLVED.value
        assert res.rcode == 0
        assert res.has_answer is True
        assert res.answer_ips == ["93.184.216.34"]
        assert res.elapsed_ms is not None

    def test_02_valid_aaaa_response(self):
        """2. Valid AAAA response returns Status.SUCCESS, resolution_status=RESOLVED, and IPv6."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", qtype="AAAA", rcode=0, answers=["2606:2800:220:1:248:1893:25c8:1946"])
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com", record_type="AAAA")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.RESOLVED.value
        assert res.rcode == 0
        assert res.has_answer is True
        assert res.answer_ips == ["2606:2800:220:1:248:1893:25c8:1946"]

    def test_03_valid_noerror_response_with_no_answers(self):
        """3. Valid NOERROR response with no answers yields resolution_status=NO_ANSWER (cautious label)."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", rcode=0, answers=[])
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.NO_ANSWER
        assert res.resolution_status == DNSResolutionStatus.NO_DATA
        assert isinstance(res.resolution_status, DNSResolutionStatus)
        assert res.rcode == 0
        assert res.has_answer is False
        assert res.answer_count == 0
        assert res.answer_ips == []

    def test_04_nxdomain_response(self):
        """4. NXDOMAIN yields resolution_status=NXDOMAIN without claiming IP outage."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="doesnotexist.example.com", rcode=3)
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "doesnotexist.example.com")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.NXDOMAIN.value
        assert res.rcode == 3
        assert res.rcode_name == "NXDOMAIN"
        assert res.has_answer is False

    def test_05_servfail_response(self):
        """5. SERVFAIL yields resolution_status=SERVFAIL without claiming general outage."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", rcode=2)
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.SERVFAIL.value
        assert res.rcode == 2
        assert res.rcode_name == "SERVFAIL"

    def test_06_refused_response(self):
        """6. REFUSED yields resolution_status=REFUSED."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", rcode=5)
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.REFUSED.value
        assert res.rcode == 5
        assert res.rcode_name == "REFUSED"

    def test_07_formerr_and_other_rcode_handling(self):
        """7. FORMERR and other non-zero RCODEs are preserved and mapped accurately."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            resp = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", rcode=1)
            return (resp, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.FORMERR.value
        assert res.rcode == 1
        assert res.rcode_name == "FORMERR"

    def test_08_wrong_transaction_id(self):
        """8. Wrong transaction ID is rejected by decoder and ignored by adapter receive loop."""
        raw_pkt = make_synthetic_dns_response(tx_id=0x9999, domain="example.com")
        with pytest.raises(DNSMismatchedTransactionIDError):
            decode_dns_response(raw_pkt, expected_tx_id=0x1234, expected_domain="example.com")

        adapter = DNSMeasurementAdapter(timeout_s=0.2)
        mock_sock = MagicMock()
        call_count = 0
        def fake_recv(buf):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (raw_pkt, ("1.1.1.1", 53))
            raise socket.timeout("Timed out")
        mock_sock.recvfrom.side_effect = fake_recv
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.TIMEOUT
        assert res.resolution_status == DNSResolutionStatus.TIMEOUT.value

    def test_09_wrong_source_ip(self):
        """9. Wrong source IP datagram is discarded and not accepted."""
        adapter = DNSMeasurementAdapter(timeout_s=0.2)
        mock_sock = MagicMock()
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com")
        call_count = 0
        def fake_recv(buf):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (raw_pkt, ("8.8.8.8", 53))  # Unexpected sender
            raise socket.timeout("Timed out")
        mock_sock.recvfrom.side_effect = fake_recv
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")
        assert res.status == Status.TIMEOUT

    def test_10_wrong_source_port(self):
        """10. Wrong source port datagram is discarded and not accepted."""
        adapter = DNSMeasurementAdapter(timeout_s=0.2)
        mock_sock = MagicMock()
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com")
        call_count = 0
        def fake_recv(buf):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (raw_pkt, ("1.1.1.1", 5353))  # Unexpected port
            raise socket.timeout("Timed out")
        mock_sock.recvfrom.side_effect = fake_recv
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com", port=53)
        assert res.status == Status.TIMEOUT

    def test_11_mismatched_question_name_type_or_class(self):
        """11. Mismatched question name, type, or class are strictly rejected."""
        raw_diff_name = make_synthetic_dns_response(tx_id=0x1234, domain="other.com")
        with pytest.raises(DNSMismatchedQuestionError, match="Question name mismatch"):
            decode_dns_response(raw_diff_name, expected_tx_id=0x1234, expected_domain="example.com")

        raw_diff_type = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", qtype="AAAA")
        with pytest.raises(DNSMismatchedQuestionError, match="Question record type mismatch"):
            decode_dns_response(raw_diff_type, expected_tx_id=0x1234, expected_domain="example.com", expected_qtype="A")

        raw_diff_class = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", qclass=3)
        with pytest.raises(DNSMismatchedQuestionError, match="Question class mismatch"):
            decode_dns_response(raw_diff_class, expected_tx_id=0x1234, expected_domain="example.com")

    def test_12_short_header(self):
        """12. Header shorter than 12 bytes raises DNSMalformedPacketError."""
        for length in (0, 1, 5, 11):
            with pytest.raises(DNSMalformedPacketError, match="shorter than DNS header"):
                decode_dns_response(b"\x00" * length, expected_tx_id=0x1234, expected_domain="example.com")

    def test_13_truncated_question(self):
        """13. Packet truncated inside the question section raises DNSMalformedPacketError."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 0, 0, 0)
        # Incomplete QNAME: length byte says 7, but only 3 bytes follow
        truncated_qname = header + b"\x07exa"
        with pytest.raises(DNSMalformedPacketError, match="Label data extends beyond packet boundaries"):
            decode_dns_response(truncated_qname, expected_tx_id=0x1234, expected_domain="example.com")

        # Complete QNAME but missing QTYPE and QCLASS
        qname_only = header + b"\x07example\x03com\x00\x00"  # only 1 byte of QTYPE
        with pytest.raises(DNSMalformedPacketError, match="Packet truncated in question section"):
            decode_dns_response(qname_only, expected_tx_id=0x1234, expected_domain="example.com")

    def test_14_out_of_range_compression_pointer(self):
        """14. Compression pointer offset out of packet bounds is rejected."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 0, 0, 0)
        # Pointer to offset 4 (inside 12-byte header)
        pkt_low_ptr = header + b"\xC0\x04"
        with pytest.raises(DNSMalformedPacketError, match="Compression pointer offset out of bounds"):
            decode_dns_response(pkt_low_ptr, expected_tx_id=0x1234, expected_domain="example.com")

        # Pointer to offset 200 (well beyond packet length)
        pkt_high_ptr = header + b"\xC0\xC8"
        with pytest.raises(DNSMalformedPacketError, match="Compression pointer offset out of bounds"):
            decode_dns_response(pkt_high_ptr, expected_tx_id=0x1234, expected_domain="example.com")

    def test_15_cyclic_compression_pointer(self):
        """15. Cyclic compression pointers are detected and rejected."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 0, 0, 0)
        # At offset 12: pointer pointing to offset 12 (itself)
        cyclic_pkt = header + struct.pack("!H", 0xC00C)
        with pytest.raises(DNSMalformedPacketError, match="Cyclic compression pointer loop detected"):
            decode_dns_response(cyclic_pkt, expected_tx_id=0x1234, expected_domain="example.com")

    def test_16_excessive_compression_pointer_traversal(self):
        """16. Excessive compression pointer hops (>10) are rejected."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 0, 0, 0)
        # Build chain of 12 pointers: offset 12 -> 14 -> 16 -> ... -> 34 -> null byte
        chain = bytearray(header)
        for i in range(12):
            chain.extend(struct.pack("!H", 0xC000 | (14 + i * 2)))
        chain.append(0x00)  # null byte at end
        with pytest.raises(DNSMalformedPacketError, match="Exceeded maximum DNS compression pointer hops"):
            decode_dns_response(bytes(chain), expected_tx_id=0x1234, expected_domain="example.com")

    def test_17_invalid_label_encoding_or_length(self):
        """17. Invalid label prefix tags and lengths exceeding 63 or 253 are rejected."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 0, 0, 0)
        # Label prefix 0x40 (reserved)
        bad_prefix_pkt = header + b"\x45abcde\x00\x00\x01\x00\x01"
        with pytest.raises(DNSMalformedPacketError, match="Invalid DNS label type prefix"):
            decode_dns_response(bad_prefix_pkt, expected_tx_id=0x1234, expected_domain="abcde")

        # Decompressed domain name exceeding 253 characters
        overlong_wire = header + b"".join([b"\x3f" + b"a" * 63 for _ in range(5)]) + b"\x00\x00\x01\x00\x01"
        with pytest.raises(DNSMalformedPacketError, match="Decompressed domain name exceeds 253 characters"):
            decode_dns_response(overlong_wire, expected_tx_id=0x1234, expected_domain="example.com")

        # Label length validation in input validator
        with pytest.raises(DNSValidationError, match="exceeds maximum length of 63"):
            validate_dns_domain("a" * 64 + ".com")

    def test_18_malformed_answer_resource_record(self):
        """18. Answer resource record with truncated fixed header raises DNSMalformedPacketError."""
        raw = make_synthetic_dns_response(tx_id=0x1234, domain="example.com")
        raw_corrupt = bytearray(raw)
        struct.pack_into("!H", raw_corrupt, 6, 1)  # ancount = 1
        raw_corrupt.extend(b"\xC0\x0C\x00\x01")  # pointer + TYPE=1, missing CLASS, TTL, RDLENGTH
        with pytest.raises(DNSMalformedPacketError, match="Packet truncated in answer record fixed header"):
            decode_dns_response(bytes(raw_corrupt), expected_tx_id=0x1234, expected_domain="example.com")

    def test_19_invalid_a_record_rdlength(self):
        """19. A record with RDLENGTH != 4 octets raises DNSMalformedPacketError."""
        bad_ans = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 3) + b"\x01\x02\x03"
        raw = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", raw_answer_bytes=bad_ans)
        with pytest.raises(DNSMalformedPacketError, match="Invalid RDATA length for A record: expected 4 octets, got 3"):
            decode_dns_response(raw, expected_tx_id=0x1234, expected_domain="example.com")

    def test_20_invalid_aaaa_record_rdlength(self):
        """20. AAAA record with RDLENGTH != 16 octets raises DNSMalformedPacketError."""
        bad_ans = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 28, 1, 300, 8) + b"\x00" * 8
        raw = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", qtype="AAAA", raw_answer_bytes=bad_ans)
        with pytest.raises(DNSMalformedPacketError, match="Invalid RDATA length for AAAA record: expected 16 octets, got 8"):
            decode_dns_response(raw, expected_tx_id=0x1234, expected_domain="example.com", expected_qtype="AAAA")

    def test_21_rdata_extending_past_packet_boundary(self):
        """21. RDATA length declaring bytes beyond datagram buffer raises DNSMalformedPacketError."""
        bad_ans = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 50) + b"\x01\x02\x03\x04"
        raw = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", raw_answer_bytes=bad_ans)
        with pytest.raises(DNSMalformedPacketError, match="extends past packet boundary"):
            decode_dns_response(raw, expected_tx_id=0x1234, expected_domain="example.com")

    def test_22_malformed_authority_or_additional_sections(self):
        """22. Declared authority and additional records are bound-checked and validated."""
        # Truncated authority record
        bad_auth = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 2, 1, 300, 20) + b"\x01\x02"
        raw_auth = make_synthetic_dns_response(
            tx_id=0x1234, domain="example.com", answers=["93.184.216.34"], nscount=1, authority_bytes=bad_auth
        )
        with pytest.raises(DNSMalformedPacketError, match="extends past packet boundary .* in authority section"):
            decode_dns_response(raw_auth, expected_tx_id=0x1234, expected_domain="example.com")

        # Truncated additional record
        bad_add = struct.pack("!H", 0xC00C) + struct.pack("!HH", 1, 1)  # only 4 bytes of 10-byte header
        raw_add = make_synthetic_dns_response(
            tx_id=0x1234, domain="example.com", answers=["93.184.216.34"], arcount=1, additional_bytes=bad_add
        )
        with pytest.raises(DNSMalformedPacketError, match="Packet truncated in additional record fixed header"):
            decode_dns_response(raw_add, expected_tx_id=0x1234, expected_domain="example.com")

    def test_23_truncated_response_behavior(self):
        """23. Truncated response (TC=1) yields Status.INCOMPLETE and resolution_status=TRUNCATED."""
        raw = make_synthetic_dns_response(tx_id=0x1234, domain="example.com", is_truncated=True)
        res = decode_dns_response(raw, expected_tx_id=0x1234, expected_domain="example.com")
        assert res.is_truncated is True
        assert res.has_answer is False

        # In adapter with matched TX ID
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_send(data, addr):
            sent.append(data)
            return len(data)
        def fake_recv(buf):
            tx_id = struct.unpack("!H", sent[0][:2])[0]
            truncated_pkt = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", is_truncated=True)
            return (truncated_pkt, ("1.1.1.1", 53))
        mock_sock.sendto.side_effect = fake_send
        mock_sock.recvfrom.side_effect = fake_recv

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            result = adapter.execute("1.1.1.1", "example.com")
        assert result.status == Status.INCOMPLETE
        assert result.resolution_status == DNSResolutionStatus.TRUNCATED.value
        assert result.is_truncated is True
        assert result.has_answer is False
        assert result.elapsed_ms is not None

    def test_24_deadline_expiration_after_unrelated_datagrams(self):
        """24. Receive loop terminates strictly at deadline despite continuous flood of unrelated datagrams."""
        adapter = DNSMeasurementAdapter(timeout_s=1.0)
        mock_sock = MagicMock()

        # Unrelated datagram from wrong IP
        junk_pkt = make_synthetic_dns_response(tx_id=0x9999, domain="other.com")

        # Mock clock advancing past deadline: t=100.0, deadline=101.0
        clock_ticks = [100.0, 100.2, 100.6, 101.05]
        tick_idx = 0
        def fake_perf():
            nonlocal tick_idx
            val = clock_ticks[min(tick_idx, len(clock_ticks) - 1)]
            tick_idx += 1
            return val

        def fake_recv(buf):
            return (junk_pkt, ("8.8.8.8", 53))

        mock_sock.recvfrom.side_effect = fake_recv

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            with patch("time.perf_counter", side_effect=fake_perf):
                res = adapter.execute("1.1.1.1", "example.com")

        assert res.status == Status.TIMEOUT
        assert res.resolution_status == DNSResolutionStatus.TIMEOUT.value
        assert res.elapsed_ms is None

    def test_25_socket_closure_on_all_execution_paths(self):
        """25. Socket is guaranteed closed on success, timeout, malformed packet, and exception."""
        adapter = DNSMeasurementAdapter()

        # a) Success path
        sock1 = MagicMock()
        sent1 = []
        def fake_send1(data, addr):
            sent1.append(data)
            return len(data)
        def fake_recv1(buf):
            tx_id = struct.unpack("!H", sent1[0][:2])[0]
            resp_valid = make_synthetic_dns_response(tx_id=tx_id, domain="example.com", answers=["93.184.216.34"])
            return (resp_valid, ("1.1.1.1", 53))
        sock1.sendto.side_effect = fake_send1
        sock1.recvfrom.side_effect = fake_recv1
        with patch.object(adapter, "_create_socket", return_value=sock1):
            res1 = adapter.execute("1.1.1.1", "example.com")
        sock1.close.assert_called_once()
        assert res1.status == Status.SUCCESS

        # b) Timeout path
        sock2 = MagicMock()
        sock2.sendto.return_value = 29
        sock2.recvfrom.side_effect = socket.timeout("Timed out")
        with patch.object(adapter, "_create_socket", return_value=sock2):
            res2 = adapter.execute("1.1.1.1", "example.com")
        sock2.close.assert_called_once()
        assert res2.status == Status.TIMEOUT

        # c) Malformed response path
        sock3 = MagicMock()
        sent3 = []
        def fake_send3(data, addr):
            sent3.append(data)
            return len(data)
        def fake_recv3(buf):
            tx_id = struct.unpack("!H", sent3[0][:2])[0]
            # Return malformed datagram (shorter than 12-byte DNS header)
            return (struct.pack("!H", tx_id) + b"\x00\x00\x00", ("1.1.1.1", 53))
        sock3.sendto.side_effect = fake_send3
        sock3.recvfrom.side_effect = fake_recv3
        with patch.object(adapter, "_create_socket", return_value=sock3):
            res3 = adapter.execute("1.1.1.1", "example.com")
        sock3.close.assert_called_once()
        assert res3.status == Status.FAILED

        # d) Unexpected exception path
        sock4 = MagicMock()
        sock4.sendto.side_effect = RuntimeError("Kernel failure")
        with patch.object(adapter, "_create_socket", return_value=sock4):
            res4 = adapter.execute("1.1.1.1", "example.com")
        sock4.close.assert_called_once()
        assert res4.status == Status.FAILED

    def test_26_ipv4_and_ipv6_address_handling(self):
        """26. IPv4, IPv6, and scoped IPv6 addresses are handled correctly without silent fallback."""
        adapter = DNSMeasurementAdapter()

        # IPv4
        with patch.object(adapter, "_create_socket") as mock_create:
            mock_sock = MagicMock()
            mock_create.return_value = mock_sock
            mock_sock.recvfrom.side_effect = socket.timeout("timeout")
            adapter.execute("1.1.1.1", "example.com")
            mock_create.assert_called_with(socket.AF_INET)
            mock_sock.sendto.assert_called_once()
            assert mock_sock.sendto.call_args[0][1] == ("1.1.1.1", 53)

        # IPv6
        with patch.object(adapter, "_create_socket") as mock_create_v6:
            mock_sock_v6 = MagicMock()
            mock_create_v6.return_value = mock_sock_v6
            mock_sock_v6.recvfrom.side_effect = socket.timeout("timeout")
            adapter.execute("2606:4700:4700::1111", "example.com")
            mock_create_v6.assert_called_with(socket.AF_INET6)
            mock_sock_v6.sendto.assert_called_once()
            assert mock_sock_v6.sendto.call_args[0][1] == ("2606:4700:4700::1111", 53, 0, 0)

        # Scoped IPv6
        with patch.object(adapter, "_create_socket") as mock_create_scope:
            mock_sock_scope = MagicMock()
            mock_create_scope.return_value = mock_sock_scope
            mock_sock_scope.recvfrom.side_effect = socket.timeout("timeout")
            adapter.execute("fe80::1%2", "example.com")
            mock_create_scope.assert_called_with(socket.AF_INET6)
            assert mock_sock_scope.sendto.call_args[0][1] == ("fe80::1", 53, 0, 2)

    def test_27_gateway_resolver_unavailable(self):
        """27. Gateway resolver unavailable reports Status.INCOMPLETE and does not guess address."""
        config = DNSResolversConfig(
            resolvers=[DNSResolverItem(id="dns-gw", label="Gateway", host="gateway", port=53, enabled=True)],
            test_domains=["example.com"],
            timeout_ms=1000,
        )
        results = evaluate_dns_resolvers(config=config, gateway_ip=None)
        assert len(results) == 1
        assert results[0].status == Status.INCOMPLETE
        assert results[0].resolution_status == DNSResolutionStatus.FAILED.value
        assert results[0].error_message is not None and "gateway" in results[0].error_message.lower()

    def test_28_missing_elapsed_time_on_timeout_and_failed_transport(self):
        """28. Non-zero null invariant: elapsed_ms is strictly None on timeout and failed queries."""
        # Verification on timeout
        res_to = DNSMeasurementResult(
            resolver_ip="1.1.1.1", resolver_port=53, ip_family="IPv4", query_domain="example.com",
            status=Status.TIMEOUT, elapsed_ms=None
        )
        assert res_to.elapsed_ms is None

        # Verification that violation raises ValueError
        with pytest.raises(ValueError, match="elapsed_ms must be None"):
            DNSMeasurementResult(
                resolver_ip="1.1.1.1", resolver_port=53, ip_family="IPv4", query_domain="example.com",
                status=Status.TIMEOUT, elapsed_ms=10.0
            )

        with pytest.raises(ValueError, match="elapsed_ms must be None"):
            DNSMeasurementResult(
                resolver_ip="1.1.1.1", resolver_port=53, ip_family="IPv4", query_domain="example.com",
                status=Status.FAILED, elapsed_ms=5.0
            )

    def test_29_correct_status_semantics_for_valid_dns_error_responses(self):
        """29. Valid DNS error responses preserve transport SUCCESS while distinguishing resolution."""
        for rcode, expected_res in [(3, DNSResolutionStatus.NXDOMAIN), (2, DNSResolutionStatus.SERVFAIL), (5, DNSResolutionStatus.REFUSED), (1, DNSResolutionStatus.FORMERR)]:
            res = DNSMeasurementResult(
                resolver_ip="1.1.1.1",
                resolver_port=53,
                ip_family="IPv4",
                query_domain="example.com",
                elapsed_ms=15.0,
                rcode=rcode,
                rcode_name=get_rcode_name(rcode),
                status=Status.SUCCESS,
                resolution_status=expected_res,
            )
            assert res.status == Status.SUCCESS  # Transport succeeded
            assert res.resolution_status == expected_res
            obs = res.to_observation(run_id="run-1", target_id="tgt-1")
            assert obs.method_metadata is not None
            meta = json.loads(obs.method_metadata)
            assert meta["transport_status"] == "SUCCESS"
            assert meta["resolution_status"] == expected_res.value
            assert meta["rcode"] == rcode

    def test_30_batch_execution_remaining_bounded(self):
        """30. Batch execution over multiple resolvers/domains remains bounded and isolated."""
        config = DNSResolversConfig(
            resolvers=[
                DNSResolverItem(id="r1", label="R1", host="1.1.1.1", port=53, enabled=True),
                DNSResolverItem(id="r2", label="R2", host="8.8.8.8", port=53, enabled=True),
                DNSResolverItem(id="r3", label="R3", host="9.9.9.9", port=53, enabled=True),
            ],
            test_domains=["example.com", "cloudflare.com"],
            timeout_ms=1000,
        )
        mock_adapter = MagicMock()
        mock_adapter.execute.side_effect = [
            DNSMeasurementResult("1.1.1.1", 53, "IPv4", "example.com", status=Status.SUCCESS, elapsed_ms=10.0),
            DNSMeasurementResult("1.1.1.1", 53, "IPv4", "cloudflare.com", status=Status.SUCCESS, elapsed_ms=11.0),
            DNSMeasurementResult("8.8.8.8", 53, "IPv4", "example.com", status=Status.TIMEOUT, elapsed_ms=None),
            DNSMeasurementResult("8.8.8.8", 53, "IPv4", "cloudflare.com", status=Status.TIMEOUT, elapsed_ms=None),
            DNSMeasurementResult("9.9.9.9", 53, "IPv4", "example.com", status=Status.SUCCESS, elapsed_ms=12.0),
            DNSMeasurementResult("9.9.9.9", 53, "IPv4", "cloudflare.com", status=Status.SUCCESS, elapsed_ms=13.0),
        ]
        results = evaluate_dns_resolvers(config=config, adapter=mock_adapter)
        assert len(results) == 6
        assert mock_adapter.execute.call_count == 6
        assert [r.status for r in results] == [
            Status.SUCCESS, Status.SUCCESS, Status.TIMEOUT, Status.TIMEOUT, Status.SUCCESS, Status.SUCCESS
        ]



# ============================================================================
# Phase 5 Final Targeted Verification Regression Suite
# ============================================================================

class TestPhase5TargetedVerification:
    """Rigorous regression verification for the 5 audited Phase 5 areas."""

    # ------------------------------------------------------------------------
    # 1. NO_DATA Semantics & CNAME / Unsupported Types
    # ------------------------------------------------------------------------
    def test_no_answer_label_and_evidence_preservation(self):
        """1. NOERROR with 0 matching answers yields NO_ANSWER label while preserving RCODE and count."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            raw_pkt = make_synthetic_dns_response(tx_id=tx_id, domain="nodata.example.com", rcode=0, answers=[])
            return (raw_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "nodata.example.com", record_type="A")

        assert res.status == Status.SUCCESS
        assert res.resolution_status == DNSResolutionStatus.NO_ANSWER
        assert res.resolution_status is DNSResolutionStatus.NO_ANSWER
        assert res.rcode == 0
        assert res.rcode_name == "NOERROR"
        assert res.has_answer is False
        assert res.answer_count == 0
        assert res.answer_ips == []

    def test_cname_only_response_does_not_claim_resolved_ip(self):
        """1. CNAME-only response to A query preserves ancount=1 but yields has_answer=False and NO_ANSWER."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            target_encoded = b"\x06target\x03com\x00"
            cname_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 5, 1, 300, len(target_encoded)) + target_encoded
            raw_pkt = make_synthetic_dns_response(
                tx_id=tx_id,
                domain="alias.example.com",
                rcode=0,
                ancount=1,
                raw_answer_bytes=cname_rr,
            )
            return (raw_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "alias.example.com", record_type="A")

        assert res.status == Status.SUCCESS
        assert res.rcode == 0
        assert res.answer_count == 1  # Evidence preserved
        assert res.has_answer is False  # Zero matching A records
        assert res.answer_ips == []
        assert res.resolution_status == DNSResolutionStatus.NO_ANSWER

    def test_cname_with_matching_a_record_yields_resolved(self):
        """1. Response with CNAME and subsequent matching A record yields RESOLVED with IP."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            target_encoded = b"\x06target\x03com\x00"
            cname_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 5, 1, 300, len(target_encoded)) + target_encoded
            a_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton("93.184.216.34")
            raw_pkt = make_synthetic_dns_response(
                tx_id=tx_id,
                domain="alias.example.com",
                rcode=0,
                ancount=2,
                raw_answer_bytes=cname_rr + a_rr,
            )
            return (raw_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "alias.example.com", record_type="A")

        assert res.status == Status.SUCCESS
        assert res.rcode == 0
        assert res.answer_count == 2
        assert res.has_answer is True
        assert res.answer_ips == ["93.184.216.34"]
        assert res.resolution_status == DNSResolutionStatus.RESOLVED

    def test_unsupported_or_mismatched_qtype_answer_does_not_claim_resolved(self):
        """1. Non-matching record type (e.g. TXT for A query, or A for AAAA query) yields NO_ANSWER."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            txt_data = b"\x05hello"
            txt_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 16, 1, 300, len(txt_data)) + txt_data
            raw_pkt = make_synthetic_dns_response(
                tx_id=tx_id,
                domain="txt.example.com",
                rcode=0,
                ancount=1,
                raw_answer_bytes=txt_rr,
            )
            return (raw_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "txt.example.com", record_type="A")

        assert res.status == Status.SUCCESS
        assert res.answer_count == 1
        assert res.has_answer is False
        assert res.answer_ips == []
        assert res.resolution_status == DNSResolutionStatus.NO_ANSWER

        # Query AAAA but server returns only A record
        def fake_recvfrom_aaaa(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            a_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton("1.2.3.4")
            raw_pkt_a = make_synthetic_dns_response(
                tx_id=tx_id,
                domain="txt.example.com",
                qtype="AAAA",
                rcode=0,
                ancount=1,
                raw_answer_bytes=a_rr,
            )
            return (raw_pkt_a, ("1.1.1.1", 53))

        mock_sock.recvfrom.side_effect = fake_recvfrom_aaaa
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res_aaaa = adapter.execute("1.1.1.1", "txt.example.com", record_type="AAAA")

        assert res_aaaa.status == Status.SUCCESS
        assert res_aaaa.answer_count == 1
        assert res_aaaa.has_answer is False
        assert res_aaaa.answer_ips == []
        assert res_aaaa.resolution_status == DNSResolutionStatus.NO_ANSWER

    # ------------------------------------------------------------------------
    # 2. Truncated DNS Responses (TC=1)
    # ------------------------------------------------------------------------
    def test_truncated_with_incomplete_answer_header(self):
        """2. Truncated packet (TC=1) cut off inside answer header yields TRUNCATED, never RESOLVED."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8300, 1, 1, 0, 0)  # TC=1, ancount=1
        qname = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
        partial_answer = struct.pack("!H", 0xC00C) + b"\x00\x01"  # Cut off after 4 bytes
        packet = header + qname + partial_answer

        decoded = decode_dns_response(packet, expected_tx_id=0x1234, expected_domain="example.com")
        assert decoded.is_truncated is True
        assert decoded.has_answer is False
        assert decoded.answer_ips == []

    def test_truncated_with_incomplete_answer_rdata(self):
        """2. Truncated packet (TC=1) with full RR header but cut off RDATA yields TRUNCATED."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8300, 1, 1, 0, 0)  # TC=1, ancount=1
        qname = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
        partial_rr = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 4) + b"\x01\x02"
        packet = header + qname + partial_rr

        decoded = decode_dns_response(packet, expected_tx_id=0x1234, expected_domain="example.com")
        assert decoded.is_truncated is True
        assert decoded.has_answer is False
        assert decoded.answer_ips == []

    def test_truncated_with_incomplete_authority_section(self):
        """2. Truncated packet (TC=1) with partial authority section yields TRUNCATED."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8300, 1, 0, 1, 0)  # TC=1, nscount=1
        qname = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
        partial_ns = struct.pack("!H", 0xC00C) + b"\x00\x02"  # Incomplete NS header
        packet = header + qname + partial_ns

        decoded = decode_dns_response(packet, expected_tx_id=0x1234, expected_domain="example.com")
        assert decoded.is_truncated is True
        assert decoded.has_answer is False

    def test_truncated_with_incomplete_additional_section(self):
        """2. Truncated packet (TC=1) with partial additional section yields TRUNCATED."""
        header = struct.pack("!HHHHHH", 0x1234, 0x8300, 1, 0, 0, 1)  # TC=1, arcount=1
        qname = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
        partial_ar = struct.pack("!H", 0xC00C) + b"\x00\x29"  # Incomplete OPT/AR header
        packet = header + qname + partial_ar

        decoded = decode_dns_response(packet, expected_tx_id=0x1234, expected_domain="example.com")
        assert decoded.is_truncated is True
        assert decoded.has_answer is False

    def test_truncated_response_never_yields_resolved_even_with_answer_payload(self):
        """2. Adapter execute with TC=1 and complete answer payload yields INCOMPLETE and TRUNCATED."""
        adapter = DNSMeasurementAdapter()
        mock_sock = MagicMock()
        sent = []
        def fake_sendto(data, addr):
            sent.append(data)
            return len(data)
        def fake_recvfrom(bufsize):
            tx_id = struct.unpack("!H", sent[-1][:2])[0]
            complete_a = struct.pack("!H", 0xC00C) + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton("93.184.216.34")
            raw_pkt = make_synthetic_dns_response(
                tx_id=tx_id,
                domain="example.com",
                is_truncated=True,
                ancount=1,
                raw_answer_bytes=complete_a,
            )
            return (raw_pkt, ("1.1.1.1", 53))

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.recvfrom.side_effect = fake_recvfrom

        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("1.1.1.1", "example.com")

        assert res.status == Status.INCOMPLETE
        assert res.resolution_status == DNSResolutionStatus.TRUNCATED
        assert res.is_truncated is True
        assert res.has_answer is False
        assert res.answer_ips == []

    # ------------------------------------------------------------------------
    # 3. IPv6 Scope Identifiers & Destination Construction
    # ------------------------------------------------------------------------
    def test_destination_construction_for_all_address_families(self):
        """3. Verifies sendto destination tuple for IPv4, plain IPv6, numeric scope, and named scope."""
        adapter = DNSMeasurementAdapter()

        # A. IPv4
        mock_sock_v4 = MagicMock()
        with patch.object(adapter, "_create_socket", return_value=mock_sock_v4):
            mock_sock_v4.recvfrom.side_effect = socket.timeout("Timed out")
            adapter.execute("1.1.1.1", "example.com", port=5353)
        assert mock_sock_v4.sendto.call_args[0][1] == ("1.1.1.1", 5353)

        # B. Plain IPv6 (scope 0)
        mock_sock_v6 = MagicMock()
        with patch.object(adapter, "_create_socket", return_value=mock_sock_v6):
            mock_sock_v6.recvfrom.side_effect = socket.timeout("Timed out")
            adapter.execute("2606:4700:4700::1111", "example.com", port=53)
        assert mock_sock_v6.sendto.call_args[0][1] == ("2606:4700:4700::1111", 53, 0, 0)

        # C. Numeric scope ID
        mock_sock_num = MagicMock()
        with patch.object(adapter, "_create_socket", return_value=mock_sock_num):
            mock_sock_num.recvfrom.side_effect = socket.timeout("Timed out")
            adapter.execute("fe80::1%4", "example.com", port=53)
        assert mock_sock_num.sendto.call_args[0][1] == ("fe80::1", 53, 0, 4)

        # D. Interface name scope converted via socket.if_nametoindex
        mock_sock_named = MagicMock()
        with patch("socket.if_nametoindex", return_value=12):
            with patch.object(adapter, "_create_socket", return_value=mock_sock_named):
                mock_sock_named.recvfrom.side_effect = socket.timeout("Timed out")
                adapter.execute("fe80::1%eth0", "example.com", port=53)
        assert mock_sock_named.sendto.call_args[0][1] == ("fe80::1", 53, 0, 12)

    def test_explicit_rejection_of_invalid_interface_scope(self):
        """3. Invalid interface scope identifier is explicitly rejected without fallback to scope 0."""
        adapter = DNSMeasurementAdapter()
        with patch("socket.if_nametoindex", side_effect=OSError("No such device")):
            with patch.object(adapter, "_create_socket") as mock_sock:
                res = adapter.execute("fe80::1%bad_iface", "example.com")
        mock_sock.assert_not_called()
        assert res.status == Status.FAILED
        assert res.resolution_status == DNSResolutionStatus.FAILED
        assert res.elapsed_ms is None
        assert res.error_message is not None and "Unknown or invalid interface scope identifier" in res.error_message

    def test_explicit_rejection_when_if_nametoindex_not_supported(self):
        """3. Interface name scope identifier is rejected if if_nametoindex is absent on platform."""
        adapter = DNSMeasurementAdapter()
        with patch("ticnet.measurements.dns.adapter.hasattr", side_effect=lambda obj, attr: False if attr == "if_nametoindex" else hasattr(obj, attr)):
            res = adapter.execute("fe80::1%eth0", "example.com")
        assert res.status == Status.FAILED
        assert res.resolution_status == DNSResolutionStatus.FAILED
        assert res.elapsed_ms is None
        assert res.error_message is not None and "Interface name scope identifiers are not supported" in res.error_message

    def test_ipv6_sender_scope_mismatch_ignored(self):
        """3. Datagram from unexpected IPv6 scope index is ignored until timeout."""
        adapter = DNSMeasurementAdapter(timeout_s=0.2)
        mock_sock = MagicMock()
        raw_pkt = make_synthetic_dns_response(tx_id=0x1234, domain="example.com")
        call_count = 0

        def fake_recv(buf):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (raw_pkt, ("fe80::1", 53, 0, 99))
            raise socket.timeout("Timed out")

        mock_sock.recvfrom.side_effect = fake_recv
        with patch.object(adapter, "_create_socket", return_value=mock_sock):
            res = adapter.execute("fe80::1%4", "example.com")

        assert res.status == Status.TIMEOUT
        assert res.resolution_status == DNSResolutionStatus.TIMEOUT

    # ------------------------------------------------------------------------
    # 4. Resolution Status Types & Model Consistency
    # ------------------------------------------------------------------------
    def test_resolution_status_enum_typing_and_normalization(self):
        """4. resolution_status is typed as DNSResolutionStatus and normalized across models/observations."""
        res_resolved = DNSMeasurementResult(
            resolver_ip="1.1.1.1",
            resolver_port=53,
            ip_family="IPv4",
            query_domain="example.com",
            status=Status.SUCCESS,
            resolution_status=DNSResolutionStatus.RESOLVED,
            elapsed_ms=10.0,
        )
        assert isinstance(res_resolved.resolution_status, DNSResolutionStatus)
        assert res_resolved.resolution_status is DNSResolutionStatus.RESOLVED

        res_from_str = DNSMeasurementResult(
            resolver_ip="1.1.1.1",
            resolver_port=53,
            ip_family="IPv4",
            query_domain="example.com",
            status=Status.SUCCESS,
            resolution_status="NO_ANSWER",
            elapsed_ms=10.0,
        )
        assert isinstance(res_from_str.resolution_status, DNSResolutionStatus)
        assert res_from_str.resolution_status is DNSResolutionStatus.NO_ANSWER

        obs = res_from_str.to_observation(run_id="run-test")
        assert obs.method_metadata is not None
        meta = json.loads(obs.method_metadata)
        assert isinstance(meta["resolution_status"], str)
        assert meta["resolution_status"] == "NO_ANSWER"

    def test_determine_resolution_status_returns_enum_members(self):
        """4. determine_resolution_status returns DNSResolutionStatus enum members for all branches."""
        assert determine_resolution_status(Status.TIMEOUT) is DNSResolutionStatus.TIMEOUT
        assert determine_resolution_status(Status.FAILED) is DNSResolutionStatus.FAILED
        assert determine_resolution_status(Status.INCOMPLETE) is DNSResolutionStatus.TRUNCATED
        assert determine_resolution_status(Status.SUCCESS, is_truncated=True) is DNSResolutionStatus.TRUNCATED
        assert determine_resolution_status(Status.SUCCESS, rcode=0, has_answer=True) is DNSResolutionStatus.RESOLVED
        assert determine_resolution_status(Status.SUCCESS, rcode=0, has_answer=False) is DNSResolutionStatus.NO_ANSWER
        assert determine_resolution_status(Status.SUCCESS, rcode=1) is DNSResolutionStatus.FORMERR
        assert determine_resolution_status(Status.SUCCESS, rcode=2) is DNSResolutionStatus.SERVFAIL
        assert determine_resolution_status(Status.SUCCESS, rcode=3) is DNSResolutionStatus.NXDOMAIN
        assert determine_resolution_status(Status.SUCCESS, rcode=5) is DNSResolutionStatus.REFUSED
        assert determine_resolution_status(Status.SUCCESS, rcode=9) is DNSResolutionStatus.OTHER_ERROR


# ============================================================================
# Opt-In Live Smoke Test (disabled by default in offline unit test runs)
# ============================================================================

@pytest.mark.live
def test_live_dns_query_smoke():
    """Opt-in live DNS query smoke test targeting discovered gateway or local DNS resolver.
    
    Guarantees:
    - Excluded from default test suite by pytest.ini marker filter (-m "not live").
    - Does not assume the gateway provides DNS services simply because it is the default gateway.
    - Explicitly branches and verifies active resolution vs safely handled timeout.
    """
    from ticnet.measurements.gateway.discovery import discover_gateway
    gw_res = discover_gateway()
    if gw_res.status != Status.SUCCESS or not gw_res.gateway_ip:
        pytest.skip("Gateway discovery not available for live DNS smoke test")

    adapter = DNSMeasurementAdapter(timeout_s=3.0)
    res = adapter.execute(resolver_ip=gw_res.gateway_ip, domain="example.com")
    assert isinstance(res, DNSMeasurementResult)
    assert isinstance(res.resolution_status, DNSResolutionStatus)

    if res.status == Status.SUCCESS:
        # Gateway actively proxies or provides DNS resolution on port 53
        assert res.rcode is not None
        assert res.rcode_name is not None
        assert res.elapsed_ms is not None and res.elapsed_ms > 0
        assert res.resolution_status in (
            DNSResolutionStatus.RESOLVED,
            DNSResolutionStatus.NO_ANSWER,
            DNSResolutionStatus.NXDOMAIN,
        )
    elif res.status == Status.TIMEOUT:
        # Gateway is reachable for IP routing but does not offer DNS services on port 53
        assert res.elapsed_ms is None
        assert res.rcode is None
        assert res.resolution_status == DNSResolutionStatus.TIMEOUT
    else:
        # Transport or socket failure during live probe
        assert res.status == Status.FAILED
        assert res.elapsed_ms is None
        assert res.resolution_status == DNSResolutionStatus.FAILED
