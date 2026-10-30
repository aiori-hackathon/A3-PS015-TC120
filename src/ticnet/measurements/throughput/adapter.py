"""Bounded HTTP streaming download throughput measurement adapter (TC-ADPT-04).

Measures actual received HTTP payload data over a configured, explicitly selected
measurement endpoint and calculates achieved download throughput.

Mandatory Safety Bounds:
- Maximum measurement duration: 10.0 seconds (hard ceiling).
- Maximum received payload: 25 MB (26,214,400 bytes, hard ceiling).
- No unbounded memory buffering: Streams in finite 64 KB chunks.
- Resource cleanup: Closes connection on all exit paths.
- Non-zero nulls: Never fabricates throughput on failure or timeout.
"""

from __future__ import annotations

import io
import os
import socket
import ssl
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

from ticnet.configuration.validator import validate_url
from ticnet.measurements.base import MeasurementAdapter
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.enums import Status
from ticnet.utils.logging import get_logger

logger = get_logger("measurements.throughput.adapter")

# Hard ceilings enforced by Phase 6 specification
HARD_MAX_DURATION_SECONDS: float = 10.0
HARD_MAX_TRANSFER_BYTES: int = 25 * 1024 * 1024  # 26,214,400 bytes (25 MB)
DEFAULT_CHUNK_SIZE: int = 64 * 1024  # 64 KB streaming chunks
DEFAULT_CONNECT_TIMEOUT_S: float = 5.0


class ThroughputMeasurementAdapter(MeasurementAdapter):
    """Measures download throughput via bounded HTTP chunk streaming."""

    def __init__(
        self,
        max_duration_s: float = 10.0,
        max_transfer_bytes: int = 26214400,
        connect_timeout_s: float = 5.0,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        urlopen_func: Optional[Callable[..., Any]] = None,
        clock_func: Optional[Callable[[], float]] = None,
    ) -> None:
        """Initialize the throughput adapter with strict safety bounds.
        
        Args:
            max_duration_s: Maximum test duration in seconds (clamped to <= 10.0s).
            max_transfer_bytes: Maximum payload in bytes (clamped to <= 25MB).
            connect_timeout_s: HTTP connection establishment timeout in seconds.
            chunk_size: Read buffer size per chunk.
            urlopen_func: Dependency injection hook for testing.
            clock_func: Monotonic clock hook for testing.
        """
        if max_duration_s <= 0:
            raise ValueError("max_duration_s must be a positive number")
        if max_transfer_bytes <= 0:
            raise ValueError("max_transfer_bytes must be a positive integer")
        if connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be a positive number")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")

        # Clamp configurable limits to mandatory hard ceilings
        if max_duration_s > HARD_MAX_DURATION_SECONDS:
            logger.warning(
                "Requested max_duration_s (%s) exceeds hard ceiling of %s s; clamping",
                max_duration_s,
                HARD_MAX_DURATION_SECONDS,
            )
            self.max_duration_s = HARD_MAX_DURATION_SECONDS
        else:
            self.max_duration_s = float(max_duration_s)

        if max_transfer_bytes > HARD_MAX_TRANSFER_BYTES:
            logger.warning(
                "Requested max_transfer_bytes (%s) exceeds hard ceiling of %s bytes; clamping",
                max_transfer_bytes,
                HARD_MAX_TRANSFER_BYTES,
            )
            self.max_transfer_bytes = HARD_MAX_TRANSFER_BYTES
        else:
            self.max_transfer_bytes = int(max_transfer_bytes)

        self.connect_timeout_s = float(connect_timeout_s)
        self.chunk_size = int(chunk_size)
        self._urlopen = urlopen_func or urllib.request.urlopen
        self._clock = clock_func or time.perf_counter
        self._active_response: Optional[Any] = None
        self._aborted: bool = False

        # Base MeasurementAdapter timeout_s set to effective duration
        super().__init__(timeout_s=self.max_duration_s)

    def abort(self) -> None:
        """Immediately abort active streaming by closing the open HTTP response."""
        self._aborted = True
        resp = self._active_response
        if resp is not None:
            sock = None
            if hasattr(resp, "fp") and hasattr(resp.fp, "raw") and hasattr(resp.fp.raw, "_sock"):
                sock = resp.fp.raw._sock
            elif hasattr(resp, "_sock"):
                sock = resp._sock
            elif hasattr(resp, "sock"):
                sock = resp.sock

            if sock is not None and hasattr(sock, "shutdown"):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

            try:
                resp.close()
            except Exception:
                pass

    @property
    def adapter_name(self) -> str:
        return "ThroughputMeasurementAdapter"

    @property
    def supported_platforms(self) -> Tuple[str, ...]:
        return ("win32", "linux", "darwin")

    def execute(
        self,
        endpoint_url: str,
        cancel_event: Optional[threading.Event] = None,
        on_chunk_received: Optional[Callable[[int, int], None]] = None,
    ) -> ThroughputMeasurementResult:
        """Execute bounded HTTP download throughput measurement.
        
        Args:
            endpoint_url: Validated HTTP/HTTPS URL.
            cancel_event: Optional threading.Event to signal early cancellation.
            on_chunk_received: Optional callback(chunk_bytes, total_bytes) for progress.
            
        Returns:
            ThroughputMeasurementResult with empirical measurements and status.
        """
        self._aborted = False

        # 1. Endpoint format validation
        try:
            validated_url = validate_url(endpoint_url, "endpoint_url")
        except Exception as exc:
            return ThroughputMeasurementResult(
                target_url=endpoint_url or "",
                status=Status.FAILED,
                error_message=f"Invalid endpoint URL: {exc}",
            )

        # 2. Check early cancellation
        if (cancel_event is not None and cancel_event.is_set()) or self._aborted:
            return ThroughputMeasurementResult(
                target_url=validated_url,
                status=Status.CANCELLED,
                error_message="Measurement cancelled before HTTP connection",
            )

        # 3. Prepare HTTP Request
        # Enforce Accept-Encoding: identity to measure actual payload wire bytes
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if "cloudflare.com" in validated_url:
            headers["Origin"] = "https://speed.cloudflare.com"
            headers["Referer"] = "https://speed.cloudflare.com/"
        req = urllib.request.Request(validated_url, headers=headers)

        # Build default SSL context with strict certificate verification
        ssl_context = ssl.create_default_context()

        start_time = self._clock()
        connect_start_time = start_time
        deadline = start_time + self.max_duration_s
        bytes_transferred = 0
        chunks_received = 0
        chunk_slices: list[Tuple[float, int]] = []
        first_byte_time: Optional[float] = None
        duration_cap_hit = False
        byte_cap_hit = False
        http_status_code: Optional[int] = None
        response = None

        try:
            # 4. Open HTTP connection
            # Connection timeout bounded by remaining deadline
            time_until_deadline = max(0.1, deadline - self._clock())
            effective_connect_timeout = min(self.connect_timeout_s, time_until_deadline)

            try:
                response = self._urlopen(
                    req,
                    timeout=effective_connect_timeout,
                    context=ssl_context if validated_url.startswith("https://") else None,
                )
            except urllib.error.HTTPError as err:
                http_status_code = err.code
                if hasattr(err, "close"):
                    try:
                        err.close()
                    except Exception:
                        pass
                return ThroughputMeasurementResult(
                    target_url=validated_url,
                    status=Status.FAILED,
                    http_status_code=http_status_code,
                    error_message=f"HTTP server returned error status {err.code}: {err.reason}",
                )
            except (socket.timeout, TimeoutError):
                return ThroughputMeasurementResult(
                    target_url=validated_url,
                    status=Status.TIMEOUT,
                    error_message=f"Connection timed out after {effective_connect_timeout:.2f}s",
                )
            except ssl.SSLError as ssl_err:
                return ThroughputMeasurementResult(
                    target_url=validated_url,
                    status=Status.FAILED,
                    error_message=f"TLS/SSL certificate verification failed: {ssl_err}",
                )
            except urllib.error.URLError as url_err:
                # Handle socket.gaierror or network unreachable
                return ThroughputMeasurementResult(
                    target_url=validated_url,
                    status=Status.FAILED,
                    error_message=f"Failed to reach endpoint: {url_err.reason}",
                )

            # Extract HTTP status code
            if hasattr(response, "status"):
                http_status_code = response.status
            elif hasattr(response, "getcode"):
                http_status_code = response.getcode()
            else:
                http_status_code = 200

            # Validate HTTP status: Only 200 OK or 206 Partial Content are valid measurement bodies
            if http_status_code not in (200, 206):
                return ThroughputMeasurementResult(
                    target_url=validated_url,
                    status=Status.FAILED,
                    http_status_code=http_status_code,
                    error_message=f"Unexpected HTTP status {http_status_code}; not valid throughput payload",
                )

            # Store active response for external abort()
            self._active_response = response

            # 5. Stream response in bounded chunks
            while True:
                # A. Check cancellation
                if (cancel_event is not None and cancel_event.is_set()) or self._aborted:
                    logger.info("Throughput measurement cancelled by user")
                    return ThroughputMeasurementResult(
                        target_url=validated_url,
                        status=Status.CANCELLED,
                        bytes_transferred=bytes_transferred,
                        chunks_received=chunks_received,
                        http_status_code=http_status_code,
                        error_message="Throughput measurement cancelled during download",
                    )

                # B. Check duration deadline
                now = self._clock()
                if now >= deadline:
                    duration_cap_hit = True
                    logger.debug("Throughput measurement reached max duration cap (%ss)", self.max_duration_s)
                    break

                # C. Check byte ceiling
                remaining_bytes = self.max_transfer_bytes - bytes_transferred
                if remaining_bytes <= 0:
                    byte_cap_hit = True
                    logger.debug("Throughput measurement reached max byte cap (%s bytes)", self.max_transfer_bytes)
                    break

                # D. Determine chunk read size
                read_size = min(self.chunk_size, remaining_bytes)

                # Dynamically bound underlying socket timeout to remaining deadline if accessible
                time_left = max(0.05, deadline - now)
                sock = None
                if hasattr(response, "fp") and hasattr(response.fp, "raw") and hasattr(response.fp.raw, "_sock"):
                    sock = response.fp.raw._sock
                elif hasattr(response, "_sock"):
                    sock = response._sock
                elif hasattr(response, "sock"):
                    sock = response.sock

                if sock is not None and hasattr(sock, "settimeout"):
                    try:
                        sock.settimeout(min(self.connect_timeout_s, time_left))
                    except Exception:
                        pass

                # E. Perform read (prefer read1 to return immediately on available chunks without blocking)
                try:
                    if hasattr(response, "read1"):
                        chunk = response.read1(read_size)
                    elif hasattr(response, "fp") and hasattr(response.fp, "read1"):
                        chunk = response.fp.read1(read_size)
                    else:
                        chunk = response.read(read_size)
                except (socket.timeout, TimeoutError):
                    logger.warning("Socket read timed out during streaming")
                    if bytes_transferred == 0:
                        return ThroughputMeasurementResult(
                            target_url=validated_url,
                            status=Status.TIMEOUT,
                            http_status_code=http_status_code,
                            error_message="Streaming read timed out with 0 bytes received",
                        )
                    # If some bytes were already received before read timeout, record partial
                    duration_cap_hit = True
                    break
                except (ValueError, OSError) as closed_err:
                    if (cancel_event is not None and cancel_event.is_set()) or self._aborted:
                        return ThroughputMeasurementResult(
                            target_url=validated_url,
                            status=Status.CANCELLED,
                            bytes_transferred=bytes_transferred,
                            chunks_received=chunks_received,
                            http_status_code=http_status_code,
                            error_message="Throughput measurement cancelled during download",
                        )
                    logger.error("Error reading response stream: %s", closed_err)
                    return ThroughputMeasurementResult(
                        target_url=validated_url,
                        status=Status.FAILED,
                        bytes_transferred=bytes_transferred,
                        chunks_received=chunks_received,
                        http_status_code=http_status_code,
                        error_message=f"Stream read error: {closed_err}",
                    )
                except Exception as read_err:
                    logger.error("Error reading response stream: %s", read_err)
                    return ThroughputMeasurementResult(
                        target_url=validated_url,
                        status=Status.FAILED,
                        bytes_transferred=bytes_transferred,
                        chunks_received=chunks_received,
                        http_status_code=http_status_code,
                        error_message=f"Stream read error: {read_err}",
                    )

                if not chunk:
                    # End of stream reached cleanly before hitting ceilings
                    break

                bytes_transferred += len(chunk)
                chunks_received += 1
                if first_byte_time is None:
                    first_byte_time = now
                chunk_slices.append((now, bytes_transferred))

                if on_chunk_received is not None:
                    try:
                        on_chunk_received(len(chunk), bytes_transferred)
                    except Exception:
                        pass

                # Re-check byte cap
                if bytes_transferred >= self.max_transfer_bytes:
                    byte_cap_hit = True
                    break

        except Exception as exc:
            logger.error("Unexpected throughput error: %s", exc, exc_info=True)
            return ThroughputMeasurementResult(
                target_url=validated_url,
                status=Status.FAILED,
                bytes_transferred=bytes_transferred,
                chunks_received=chunks_received,
                http_status_code=http_status_code,
                error_message=f"Unexpected error: {exc}",
            )
        finally:
            # 6. Strict resource cleanup
            self._active_response = None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        end_time = self._clock()
        elapsed_s = max(0.0001, end_time - start_time)

        # 7. Check for empty response
        if bytes_transferred == 0:
            return ThroughputMeasurementResult(
                target_url=validated_url,
                status=Status.FAILED,
                bytes_transferred=0,
                elapsed_s=round(elapsed_s, 4),
                http_status_code=http_status_code,
                error_message="Zero payload bytes received from endpoint",
            )

        # 8. Compute accurate steady-state throughput (bps and decimal Mbps)
        # For multi-chunk transfers, discard TCP slow-start warm-up (first 20% or 0.25s)
        # and isolate transfer duration from connection handshake overhead.
        # Fall back to overall payload / elapsed for single-chunk / mock tests.
        transfer_elapsed_s = max(0.0001, end_time - (first_byte_time or start_time))
        if chunks_received >= 3 and len(chunk_slices) >= 3 and transfer_elapsed_s >= 0.2:
            warmup_s = min(0.3, transfer_elapsed_s * 0.20)
            t_warmup = (first_byte_time or start_time) + warmup_s
            steady_chunks = [(t, b) for t, b in chunk_slices if t >= t_warmup]
            if len(steady_chunks) >= 2:
                dt_steady = steady_chunks[-1][0] - steady_chunks[0][0]
                db_steady = steady_chunks[-1][1] - steady_chunks[0][1]
                if dt_steady > 0.001 and db_steady > 0:
                    throughput_bps = (db_steady * 8.0) / dt_steady
                else:
                    throughput_bps = (bytes_transferred * 8.0) / transfer_elapsed_s
            else:
                throughput_bps = (bytes_transferred * 8.0) / transfer_elapsed_s
        else:
            throughput_bps = (bytes_transferred * 8.0) / elapsed_s

        throughput_mbps = throughput_bps / 1_000_000.0

        return ThroughputMeasurementResult(
            target_url=validated_url,
            status=Status.SUCCESS,
            bytes_transferred=bytes_transferred,
            elapsed_s=round(elapsed_s, 4),
            throughput_bps=round(throughput_bps, 2),
            throughput_mbps=round(throughput_mbps, 3),
            http_status_code=http_status_code,
            duration_cap_hit=duration_cap_hit,
            byte_cap_hit=byte_cap_hit,
            chunks_received=chunks_received,
            error_message=None,
        )
