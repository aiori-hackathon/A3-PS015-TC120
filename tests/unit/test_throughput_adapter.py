"""Unit tests for Throughput Measurement Adapter (TC-ADPT-04).

Verifies bounded HTTP streaming, payload accounting, throughput calculation,
hard duration/byte ceilings (10s, 25MB), resource cleanup, error handling,
cancellation, and non-zero null invariants.
"""

from __future__ import annotations

import io
import json
import socket
import ssl
import threading
import time
from typing import Any, List, Optional
import urllib.error
import urllib.request
import pytest

from ticnet.measurements.throughput.adapter import (
    DEFAULT_CHUNK_SIZE,
    HARD_MAX_DURATION_SECONDS,
    HARD_MAX_TRANSFER_BYTES,
    ThroughputMeasurementAdapter,
)
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.entities import TestRun
from ticnet.models.enums import MeasurementStatus, RunStatus, Status
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository


class MockHTTPResponse:
    """Deterministic test double for urllib HTTP response stream."""

    def __init__(
        self,
        chunks: List[bytes],
        status_code: int = 200,
        read_delay_s: float = 0.0,
        read_side_effect: Optional[Exception] = None,
    ) -> None:
        self._chunks = list(chunks)
        self.status = status_code
        self.code = status_code
        self._read_delay = read_delay_s
        self._read_side_effect = read_side_effect
        self.closed = False
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self._read_side_effect is not None:
            raise self._read_side_effect
        if self._read_delay > 0:
            time.sleep(self._read_delay)
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if size > 0 and len(chunk) > size:
            # Split chunk if requested size is smaller
            remaining = chunk[size:]
            self._chunks.insert(0, remaining)
            return chunk[:size]
        return chunk

    def close(self) -> None:
        self.closed = True

    def read1(self, size: int = -1) -> bytes:
        return self.read(size)


# ============================================================================
# Section 1: Configuration, Validation, and Hard Caps
# ============================================================================

class TestThroughputConfigurationAndCaps:
    """Verifies bounds clamping and input validation."""

    def test_default_configuration_within_bounds(self):
        adapter = ThroughputMeasurementAdapter()
        assert adapter.max_duration_s == HARD_MAX_DURATION_SECONDS
        assert adapter.max_transfer_bytes == HARD_MAX_TRANSFER_BYTES
        assert adapter.chunk_size == DEFAULT_CHUNK_SIZE

    def test_clamps_excessive_duration_to_hard_ceiling(self):
        adapter = ThroughputMeasurementAdapter(max_duration_s=60.0)
        assert adapter.max_duration_s == HARD_MAX_DURATION_SECONDS

    def test_clamps_excessive_bytes_to_hard_ceiling(self):
        adapter = ThroughputMeasurementAdapter(max_transfer_bytes=100 * 1024 * 1024)
        assert adapter.max_transfer_bytes == HARD_MAX_TRANSFER_BYTES

    def test_honors_lower_configurable_limits(self):
        adapter = ThroughputMeasurementAdapter(
            max_duration_s=3.5,
            max_transfer_bytes=1024 * 1024,
            chunk_size=16384,
        )
        assert adapter.max_duration_s == 3.5
        assert adapter.max_transfer_bytes == 1024 * 1024
        assert adapter.chunk_size == 16384

    def test_rejects_negative_or_zero_parameters(self):
        with pytest.raises(ValueError, match="max_duration_s must be a positive number"):
            ThroughputMeasurementAdapter(max_duration_s=0)

        with pytest.raises(ValueError, match="max_transfer_bytes must be a positive integer"):
            ThroughputMeasurementAdapter(max_transfer_bytes=-100)

        with pytest.raises(ValueError, match="connect_timeout_s must be a positive number"):
            ThroughputMeasurementAdapter(connect_timeout_s=-1.0)

        with pytest.raises(ValueError, match="chunk_size must be a positive integer"):
            ThroughputMeasurementAdapter(chunk_size=0)

    def test_rejects_invalid_endpoint_urls(self):
        adapter = ThroughputMeasurementAdapter()
        # Non-http/https
        res = adapter.execute("ftp://speed.example.com/file")
        assert res.status == Status.FAILED
        assert "Invalid endpoint URL" in (res.error_message or "")
        assert res.throughput_bps is None

        # Empty string
        res_empty = adapter.execute("")
        assert res_empty.status == Status.FAILED
        assert "Invalid endpoint URL" in (res_empty.error_message or "")


# ============================================================================
# Section 2: Successful Streaming and Throughput Calculations
# ============================================================================

class TestThroughputMeasurementExecution:
    """Verifies streaming chunks, calculations, and resource closure."""

    def test_successful_streaming_single_chunk(self):
        data = b"X" * 1_000_000  # 1 MB
        mock_resp = MockHTTPResponse([data])

        fake_time = [100.0, 100.0, 100.5, 100.5]  # start=100.0, end=100.5 -> 0.5s
        time_idx = 0
        def fake_clock():
            nonlocal time_idx
            val = fake_time[min(time_idx, len(fake_time) - 1)]
            time_idx += 1
            return val

        adapter = ThroughputMeasurementAdapter(
            chunk_size=1_000_000,
            urlopen_func=lambda req, **kwargs: mock_resp,
            clock_func=fake_clock,
        )

        res = adapter.execute("https://speed.example.com/test")
        assert res.status == Status.SUCCESS
        assert res.bytes_transferred == 1_000_000
        assert res.chunks_received == 1
        assert res.http_status_code == 200
        assert res.elapsed_s == 0.5
        # Throughput formula: (1,000,000 * 8) / 0.5 = 16,000,000 bps = 16.0 Mbps
        assert res.throughput_bps == 16_000_000.0
        assert res.throughput_mbps == 16.0
        assert mock_resp.closed is True

    def test_successful_multiple_streamed_chunks(self):
        chunk1 = b"A" * 65536
        chunk2 = b"B" * 65536
        chunk3 = b"C" * 65536
        mock_resp = MockHTTPResponse([chunk1, chunk2, chunk3])

        fake_time = [10.0, 10.0, 10.1, 10.2, 10.3, 11.0]
        time_idx = 0
        def fake_clock():
            nonlocal time_idx
            val = fake_time[min(time_idx, len(fake_time) - 1)]
            time_idx += 1
            return val

        adapter = ThroughputMeasurementAdapter(
            urlopen_func=lambda req, **kwargs: mock_resp,
            clock_func=fake_clock,
        )

        progress_calls = []
        def on_chunk(chunk_len, total):
            progress_calls.append((chunk_len, total))

        res = adapter.execute("https://speed.example.com/test", on_chunk_received=on_chunk)
        assert res.status == Status.SUCCESS
        assert res.bytes_transferred == 65536 * 3
        assert res.chunks_received == 3
        assert len(progress_calls) == 3
        assert progress_calls[-1] == (65536, 65536 * 3)
        assert mock_resp.closed is True

    def test_empty_response_body_yields_failed_status(self):
        mock_resp = MockHTTPResponse([])  # 0 bytes
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        res = adapter.execute("https://speed.example.com/empty")
        assert res.status == Status.FAILED
        assert res.bytes_transferred == 0
        assert res.throughput_bps is None
        assert res.throughput_mbps is None
        assert "Zero payload bytes received" in (res.error_message or "")
        assert mock_resp.closed is True

    def test_http_error_status_does_not_count_as_valid_throughput(self):
        http_err = urllib.error.HTTPError(
            url="https://speed.example.com/error",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b"Custom 404 HTML body that must not be counted as throughput"),
        )
        def fake_urlopen(req, **kwargs):
            raise http_err

        adapter = ThroughputMeasurementAdapter(urlopen_func=fake_urlopen)
        res = adapter.execute("https://speed.example.com/error")

        assert res.status == Status.FAILED
        assert res.http_status_code == 404
        assert res.bytes_transferred == 0
        assert res.throughput_bps is None
        assert res.throughput_mbps is None
        assert "HTTP server returned error status 404" in (res.error_message or "")

    def test_unexpected_http_status_code_rejected(self):
        mock_resp = MockHTTPResponse([b"redirect body"], status_code=302)
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        res = adapter.execute("https://speed.example.com/redirect")
        assert res.status == Status.FAILED
        assert res.http_status_code == 302
        assert res.throughput_bps is None
        assert "Unexpected HTTP status 302" in (res.error_message or "")
        assert mock_resp.closed is True


# ============================================================================
# Section 3: Hard Safety Ceilings (Duration & Byte Cap Enforcement)
# ============================================================================

class TestThroughputCeilingsAndLimits:
    """Verifies that 10s and 25MB limits stop download promptly."""

    def test_duration_cap_stops_streaming(self):
        # Infinite generator of 64KB chunks
        chunk = b"D" * 65536
        chunks = [chunk] * 100
        mock_resp = MockHTTPResponse(chunks)

        # Monotonic clock ticks past the 10s deadline after 2 chunks
        clock_ticks = [0.0, 0.0, 1.0, 5.0, 10.1, 10.5]
        tick_idx = 0
        def fake_clock():
            nonlocal tick_idx
            val = clock_ticks[min(tick_idx, len(clock_ticks) - 1)]
            tick_idx += 1
            return val

        adapter = ThroughputMeasurementAdapter(
            max_duration_s=10.0,
            urlopen_func=lambda req, **kwargs: mock_resp,
            clock_func=fake_clock,
        )

        res = adapter.execute("https://speed.example.com/large")
        assert res.status == Status.SUCCESS
        assert res.duration_cap_hit is True
        assert res.bytes_transferred > 0
        assert res.throughput_bps is not None
        assert mock_resp.closed is True

    def test_byte_cap_stops_streaming(self):
        # 10 chunks of 1 MB = 10 MB total; set byte cap to 2 MB
        chunks = [b"B" * 1_000_000] * 10
        mock_resp = MockHTTPResponse(chunks)

        adapter = ThroughputMeasurementAdapter(
            max_transfer_bytes=2_000_000,
            chunk_size=1_000_000,
            urlopen_func=lambda req, **kwargs: mock_resp,
        )

        res = adapter.execute("https://speed.example.com/file")
        assert res.status == Status.SUCCESS
        assert res.byte_cap_hit is True
        assert res.bytes_transferred == 2_000_000
        assert res.chunks_received == 2
        assert mock_resp.closed is True

    def test_enforces_hard_byte_cap_even_if_configured_higher(self):
        # Try to configure 50MB; clamped to 25MB (26,214,400 bytes)
        adapter = ThroughputMeasurementAdapter(max_transfer_bytes=50 * 1024 * 1024)
        assert adapter.max_transfer_bytes == HARD_MAX_TRANSFER_BYTES

        chunks = [b"C" * 10_000_000] * 5  # 50 MB total available
        mock_resp = MockHTTPResponse(chunks)
        adapter._urlopen = lambda req, **kwargs: mock_resp

        res = adapter.execute("https://speed.example.com/huge")
        assert res.status == Status.SUCCESS
        assert res.byte_cap_hit is True
        assert res.bytes_transferred == HARD_MAX_TRANSFER_BYTES
        assert mock_resp.closed is True

    def test_slow_drip_server_stopped_at_deadline(self):
        # Slow-drip server emits 100 bytes per chunk
        chunks = [b"S" * 100] * 50
        mock_resp = MockHTTPResponse(chunks)

        # Monotonic clock advances 1 second per check; crosses 10.0s deadline at tick 11
        clock_ticks = [0.0, 0.0] + [float(i) for i in range(1, 15)]
        tick_idx = 0
        def fake_clock():
            nonlocal tick_idx
            val = clock_ticks[min(tick_idx, len(clock_ticks) - 1)]
            tick_idx += 1
            return val

        adapter = ThroughputMeasurementAdapter(
            chunk_size=100,
            urlopen_func=lambda req, **kwargs: mock_resp,
            clock_func=fake_clock,
        )

        res = adapter.execute("https://speed.example.com/slowdrip")
        assert res.status == Status.SUCCESS
        assert res.duration_cap_hit is True
        # Must have transferred some data but terminated cleanly at deadline
        assert res.bytes_transferred > 0
        assert mock_resp.closed is True

    def test_exact_byte_boundary_conditions(self):
        # Case A: Exact ceiling (26,214,400 bytes)
        cap = HARD_MAX_TRANSFER_BYTES
        mock_exact = MockHTTPResponse([b"E" * cap])
        adapter_exact = ThroughputMeasurementAdapter(
            chunk_size=cap,
            urlopen_func=lambda req, **kwargs: mock_exact,
        )
        res_exact = adapter_exact.execute("https://speed.example.com/exact")
        assert res_exact.bytes_transferred == cap
        assert res_exact.byte_cap_hit is True
        assert mock_exact.closed is True

        # Case B: 1 byte over ceiling (26,214,401 bytes)
        mock_over = MockHTTPResponse([b"O" * (cap + 1)])
        adapter_over = ThroughputMeasurementAdapter(
            chunk_size=cap + 1,
            urlopen_func=lambda req, **kwargs: mock_over,
        )
        res_over = adapter_over.execute("https://speed.example.com/over")
        assert res_over.bytes_transferred == cap
        assert res_over.byte_cap_hit is True
        assert mock_over.closed is True

        # Case C: 1 byte under ceiling (26,214,399 bytes)
        mock_under = MockHTTPResponse([b"U" * (cap - 1)])
        adapter_under = ThroughputMeasurementAdapter(
            chunk_size=cap,
            urlopen_func=lambda req, **kwargs: mock_under,
        )
        res_under = adapter_under.execute("https://speed.example.com/under")
        assert res_under.bytes_transferred == cap - 1
        assert res_under.byte_cap_hit is False
        assert mock_under.closed is True

    def test_exact_duration_boundary_conditions(self):
        # Clock ticks: start=0.0, connect=0.0, read1=5.0, read2=10.0 (exact deadline reached)
        ticks = [0.0, 0.0, 5.0, 10.0, 10.0]
        t_idx = 0
        def fake_clock():
            nonlocal t_idx
            v = ticks[min(t_idx, len(ticks) - 1)]
            t_idx += 1
            return v

        mock_resp = MockHTTPResponse([b"A" * 1024, b"B" * 1024, b"C" * 1024])
        adapter = ThroughputMeasurementAdapter(
            max_duration_s=10.0,
            urlopen_func=lambda req, **kwargs: mock_resp,
            clock_func=fake_clock,
        )
        res = adapter.execute("https://speed.example.com/duration_exact")
        assert res.status == Status.SUCCESS
        assert res.duration_cap_hit is True
        assert mock_resp.closed is True

    def test_stalled_body_read_timeout_after_partial_transfer(self):
        # 1 chunk succeeds, second chunk raises socket.timeout
        mock_resp = MockHTTPResponse([b"P" * 65536])
        orig_read = mock_resp.read
        def read_with_stall(size=-1):
            if mock_resp.read_calls >= 1:
                raise socket.timeout("Read socket timed out")
            return orig_read(size)

        mock_resp.read = read_with_stall
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        res = adapter.execute("https://speed.example.com/stalled_body")
        assert res.status == Status.SUCCESS
        assert res.duration_cap_hit is True
        assert res.bytes_transferred == 65536
        assert res.throughput_bps is not None
        assert mock_resp.closed is True

    def test_http_error_closes_response_fp(self):
        fp_closed = False
        class MockErrorStream(io.BytesIO):
            def close(self):
                nonlocal fp_closed
                fp_closed = True
                super().close()

        err_fp = MockErrorStream(b"Error payload")
        http_err = urllib.error.HTTPError(
            url="https://speed.example.com/500",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=err_fp,
        )
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: (_ for _ in ()).throw(http_err))
        res = adapter.execute("https://speed.example.com/500")

        assert res.status == Status.FAILED
        assert res.http_status_code == 500
        assert fp_closed is True


# ============================================================================
# Section 4: Timeouts, Network Failures, and Cancellation
# ============================================================================

class TestThroughputFailuresAndTimeouts:
    """Verifies timeout handling, exceptions, and non-zero null invariants."""

    def test_connection_timeout_handling(self):
        def fake_urlopen(req, **kwargs):
            raise socket.timeout("Connection timed out")

        adapter = ThroughputMeasurementAdapter(urlopen_func=fake_urlopen)
        res = adapter.execute("https://speed.example.com/timeout")

        assert res.status == Status.TIMEOUT
        assert res.bytes_transferred == 0
        assert res.elapsed_s is None
        assert res.throughput_bps is None
        assert res.throughput_mbps is None
        assert "Connection timed out" in (res.error_message or "")

    def test_streaming_read_timeout_with_zero_bytes(self):
        mock_resp = MockHTTPResponse(
            chunks=[],
            read_side_effect=socket.timeout("Read timed out"),
        )
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        res = adapter.execute("https://speed.example.com/slow")
        assert res.status == Status.TIMEOUT
        assert res.bytes_transferred == 0
        assert res.throughput_bps is None
        assert mock_resp.closed is True

    def test_tls_certificate_verification_failure(self):
        ssl_err = ssl.SSLCertVerificationError("Certificate verify failed: self-signed certificate")
        def fake_urlopen(req, **kwargs):
            raise ssl_err

        adapter = ThroughputMeasurementAdapter(urlopen_func=fake_urlopen)
        res = adapter.execute("https://badssl.example.com/")

        assert res.status == Status.FAILED
        assert res.throughput_bps is None
        assert "TLS/SSL certificate verification failed" in (res.error_message or "")

    def test_dns_or_unreachable_network_failure(self):
        def fake_urlopen(req, **kwargs):
            raise urllib.error.URLError("Name or service not known")

        adapter = ThroughputMeasurementAdapter(urlopen_func=fake_urlopen)
        res = adapter.execute("https://nonexistent.domain.example.com/")

        assert res.status == Status.FAILED
        assert res.throughput_bps is None
        assert "Failed to reach endpoint" in (res.error_message or "")

    def test_cancellation_before_connection(self):
        cancel_ev = threading.Event()
        cancel_ev.set()  # Already cancelled

        adapter = ThroughputMeasurementAdapter()
        res = adapter.execute("https://speed.example.com/test", cancel_event=cancel_ev)

        assert res.status == Status.CANCELLED
        assert res.throughput_bps is None
        assert "cancelled before HTTP connection" in (res.error_message or "")

    def test_cancellation_during_streaming(self):
        cancel_ev = threading.Event()
        chunks = [b"A" * 65536, b"B" * 65536, b"C" * 65536]
        mock_resp = MockHTTPResponse(chunks)

        def on_chunk(chunk_len, total):
            if total >= 65536:
                cancel_ev.set()

        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)
        res = adapter.execute(
            "https://speed.example.com/test",
            cancel_event=cancel_ev,
            on_chunk_received=on_chunk,
        )

        assert res.status == Status.CANCELLED
        assert res.bytes_transferred >= 65536
        assert res.throughput_bps is None  # Non-zero null invariant on cancelled
        assert mock_resp.closed is True

    def test_resource_closed_on_unexpected_read_exception(self):
        mock_resp = MockHTTPResponse(
            chunks=[],
            read_side_effect=ConnectionResetError("Connection reset by peer"),
        )
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        res = adapter.execute("https://speed.example.com/reset")
        assert res.status == Status.FAILED
        assert res.throughput_bps is None
        assert "Stream read error" in (res.error_message or "")
        assert mock_resp.closed is True


# ============================================================================
# Section 5: Persistence Mapping & Invariants
# ============================================================================

class TestThroughputPersistenceMapping:
    """Verifies mapping of ThroughputMeasurementResult to Observation entities and SQLite."""

    def test_observation_mapping_success(self):
        res = ThroughputMeasurementResult(
            target_url="https://speed.cloudflare.com/__down?bytes=10000000",
            status=Status.SUCCESS,
            bytes_transferred=10_000_000,
            elapsed_s=1.25,
            throughput_bps=64_000_000.0,
            throughput_mbps=64.0,
            http_status_code=200,
            duration_cap_hit=False,
            byte_cap_hit=False,
            chunks_received=153,
        )

        obs = res.to_observation(run_id="run-test-tp")
        assert obs.run_id == "run-test-tp"
        assert obs.metric_name == "download_throughput"
        assert obs.unit == "Mbps"
        assert obs.status == MeasurementStatus.SUCCESS
        assert obs.value_numeric == 64.0
        assert obs.duration_ms == 1250.0

        meta = json.loads(obs.method_metadata)
        assert meta["throughput_bps"] == 64_000_000.0
        assert meta["bytes_transferred"] == 10_000_000
        assert meta["http_status_code"] == 200

    def test_observation_mapping_timeout(self):
        res = ThroughputMeasurementResult(
            target_url="https://speed.example.com/timeout",
            status=Status.TIMEOUT,
            bytes_transferred=0,
            elapsed_s=None,
            throughput_bps=None,
            throughput_mbps=None,
            error_message="Connection timed out",
        )

        obs = res.to_observation(run_id="run-test-timeout")
        assert obs.metric_name == "download_throughput"
        assert obs.status == MeasurementStatus.TIMEOUT
        assert obs.value_numeric is None  # Non-zero null invariant

    def test_persistence_in_sqlite(self, tmp_path):
        db_file = tmp_path / "test_tp.db"
        db = DatabaseManager(str(db_file))
        db.run_migrations()
        run_repo = RunRepository(db)
        obs_repo = ObservationRepository(db)

        run = TestRun(
            run_id="run-tp-db",
            start_time_utc="2026-10-22T20:00:00Z",
            status=RunStatus.RUNNING,
            app_version="1.0.0",
            config_version="1.0.0",
        )
        run_repo.create_run(run)

        res = ThroughputMeasurementResult(
            target_url="https://speed.example.com/test",
            status=Status.SUCCESS,
            bytes_transferred=5_000_000,
            elapsed_s=2.0,
            throughput_bps=20_000_000.0,
            throughput_mbps=20.0,
            http_status_code=200,
        )
        obs = res.to_observation(run_id=run.run_id)
        obs_repo.create_observation(obs)

        loaded = obs_repo.get_observations_for_run(run.run_id)
        assert len(loaded) == 1
        assert loaded[0].metric_name == "download_throughput"
        assert loaded[0].value_numeric == 20.0
        assert loaded[0].status == MeasurementStatus.SUCCESS

    def test_abort_closes_active_response(self):
        mock_resp = MockHTTPResponse([b"chunk1", b"chunk2"])
        adapter = ThroughputMeasurementAdapter(urlopen_func=lambda req, **kwargs: mock_resp)

        # Simulate active response assignment and abort()
        adapter._active_response = mock_resp
        assert mock_resp.closed is False
        adapter.abort()
        assert mock_resp.closed is True


# ============================================================================
# Section 6: Real Local Socket / Server Tests (Deadline & Cancellation)
# ============================================================================

class _LocalLoopbackServer:
    """Ephemeral loopback TCP server for deterministic real-socket testing."""

    def __init__(self, handler_func):
        self.handler_func = handler_func
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()

    def _serve(self):
        try:
            self.sock.settimeout(2.0)
            conn, _ = self.sock.accept()
            try:
                self.handler_func(conn, self.stop_event)
            finally:
                conn.close()
        except (socket.timeout, OSError):
            pass
        finally:
            self.sock.close()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1.0)


class TestLocalServerEdgeCases:
    """Verifies real socket behavior: stalled headers, stalled reads, slow-drip, and cancellation."""

    def test_local_server_stalled_headers_times_out(self):
        def handler(conn, stop_ev):
            # Read request, but do not send headers back
            try:
                conn.recv(1024)
            except Exception:
                pass
            stop_ev.wait(timeout=2.0)

        server = _LocalLoopbackServer(handler)
        server.start()
        try:
            adapter = ThroughputMeasurementAdapter(max_duration_s=0.3, connect_timeout_s=0.3)
            res = adapter.execute(f"http://127.0.0.1:{server.port}/stalled_headers")

            assert res.status == Status.TIMEOUT
            assert res.bytes_transferred == 0
            assert res.throughput_bps is None
            assert "timed out" in (res.error_message or "").lower()
        finally:
            server.stop()

    def test_local_server_stalled_body_read_bounded_by_deadline(self):
        def handler(conn, stop_ev):
            try:
                conn.recv(1024)
                # Send headers and 500 bytes of body, then stall
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 1000000\r\n"
                    b"Connection: close\r\n\r\n" + (b"B" * 500)
                )
            except Exception:
                pass
            stop_ev.wait(timeout=2.0)

        server = _LocalLoopbackServer(handler)
        server.start()
        try:
            adapter = ThroughputMeasurementAdapter(max_duration_s=0.4, connect_timeout_s=0.4)
            start_t = time.perf_counter()
            res = adapter.execute(f"http://127.0.0.1:{server.port}/stalled_body")
            elapsed = time.perf_counter() - start_t

            # Verify it bounded the duration and did not hang indefinitely
            assert elapsed < 1.5
            assert res.status == Status.SUCCESS
            assert res.bytes_transferred == 500
            assert res.duration_cap_hit is True
            assert res.throughput_bps is not None
        finally:
            server.stop()

    def test_local_server_slow_drip_stops_at_deadline(self):
        def handler(conn, stop_ev):
            try:
                conn.recv(1024)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Connection: close\r\n\r\n"
                )
                while not stop_ev.is_set():
                    conn.sendall(b"D" * 64)
                    time.sleep(0.04)
            except Exception:
                pass

        server = _LocalLoopbackServer(handler)
        server.start()
        try:
            adapter = ThroughputMeasurementAdapter(max_duration_s=0.3, connect_timeout_s=0.3)
            start_t = time.perf_counter()
            res = adapter.execute(f"http://127.0.0.1:{server.port}/slow_drip")
            elapsed = time.perf_counter() - start_t

            assert elapsed < 1.0
            assert res.status == Status.SUCCESS
            assert res.duration_cap_hit is True
            assert res.bytes_transferred > 0
            assert res.throughput_bps is not None
        finally:
            server.stop()

    def test_local_server_cancellation_while_read_blocked(self):
        def handler(conn, stop_ev):
            try:
                conn.recv(1024)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 1000000\r\n"
                    b"Connection: close\r\n\r\n" + (b"I" * 128)
                )
            except Exception:
                pass
            stop_ev.wait(timeout=2.0)

        server = _LocalLoopbackServer(handler)
        server.start()
        try:
            adapter = ThroughputMeasurementAdapter(max_duration_s=5.0, connect_timeout_s=5.0)
            cancel_ev = threading.Event()
            chunk_ev = threading.Event()
            res_holder = []

            def run():
                r = adapter.execute(
                    f"http://127.0.0.1:{server.port}/block_read",
                    cancel_event=cancel_ev,
                    on_chunk_received=lambda c, t: chunk_ev.set(),
                )
                res_holder.append(r)

            th = threading.Thread(target=run, daemon=True)
            th.start()

            # Wait for first chunk to be processed and verify thread is in blocked read
            chunk_ev.wait(timeout=1.0)
            time.sleep(0.05)

            # Cancel and abort active streaming
            cancel_ev.set()
            adapter.abort()

            # Bounded join to verify thread terminates promptly
            th.join(timeout=1.0)
            assert th.is_alive() is False

            assert len(res_holder) == 1
            res = res_holder[0]
            assert res.status == Status.CANCELLED
            assert res.throughput_bps is None
        finally:
            server.stop()


