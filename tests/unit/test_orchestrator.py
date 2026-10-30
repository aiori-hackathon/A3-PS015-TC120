"""Unit tests for WorkflowOrchestrator (Phase 7 - SRS-F-008, SRS-F-020).

Tests staged workflow execution, skip criteria, cancellation handling,
configuration validation, exception recovery, and SQLite persistence.
"""

from __future__ import annotations

import dataclasses
import json
import queue
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional
import pytest

from ticnet.models.entities import Observation

from ticnet.configuration.loader import load_app_config
from ticnet.configuration.validator import (
    AppConfig,
    TargetItem,
    ExternalTargetsConfig,
    GatewayConfig,
    TargetsConfig,
)
from ticnet.measurements.base import GatewayDiscoveryResult
from ticnet.measurements.dns.adapter import DNSMeasurementAdapter
from ticnet.measurements.dns.models import DNSMeasurementResult, DNSResolutionStatus
from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter
from ticnet.measurements.load.adapter import LoadedLatencyAdapter
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult, PingProbeObservation
from ticnet.measurements.throughput.adapter import ThroughputMeasurementAdapter
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.enums import (
    MeasurementStatus,
    RunMode,
    RunStatus,
    StageStatus,
    Status,
    TargetType,
)
from ticnet.persistence.database import DatabaseManager
from ticnet.persistence.repositories.observation_repo import ObservationRepository
from ticnet.persistence.repositories.run_repo import RunRepository
from ticnet.persistence.repositories.summary_repo import SummaryRepository
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType
from ticnet.workflow.orchestrator import WorkflowOrchestrator


# ============================================================================
# Mock Adapter Implementations
# ============================================================================

class MockGatewayDiscoveryAdapter(GatewayDiscoveryAdapter):
    def __init__(
        self,
        status: Status = Status.SUCCESS,
        gateway_ip: str = "192.168.1.1",
        interface_name: str = "eth0",
        ip_family: str = "IPv4",
        error_message: Optional[str] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        super().__init__()
        self._status = status
        self._gateway_ip = gateway_ip
        self._interface_name = interface_name
        self._ip_family = ip_family
        self._error_message = error_message
        self._raise_exc = raise_exc
        self.execute_calls: List[Dict[str, Any]] = []

    def execute(self, *args, **kwargs) -> GatewayDiscoveryResult:
        self.execute_calls.append({"args": args, "kwargs": kwargs})
        if self._raise_exc:
            raise self._raise_exc
        return GatewayDiscoveryResult(
            status=self._status,
            gateway_ip=self._gateway_ip if self._status == Status.SUCCESS else None,
            ip_family=self._ip_family,
            interface_name=self._interface_name,
            error_message=self._error_message,
        )


class MockPingMeasurementAdapter(PingMeasurementAdapter):
    def __init__(
        self,
        default_status: Status = Status.SUCCESS,
        loss_pct: float = 0.0,
        rtts: Optional[List[float]] = None,
        per_target_results: Optional[Dict[str, PingMeasurementResult]] = None,
        raise_exc_for: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.default_status = default_status
        self.loss_pct = loss_pct
        self.rtts = rtts or [10.0, 10.0, 10.0, 10.0]
        self.per_target_results = per_target_results or {}
        self.raise_exc_for = raise_exc_for
        self.execute_calls: List[Dict[str, Any]] = []

    def execute(
        self,
        target_ip: str,
        count: int = 4,
        per_probe_timeout_ms: int = 1000,
        target_type: TargetType = TargetType.GATEWAY,
        probe_count: Optional[int] = None,
        interval_ms: Optional[int] = None,
        **kwargs: Any,
    ) -> PingMeasurementResult:
        self.execute_calls.append({
            "target_ip": target_ip,
            "count": count,
            "per_probe_timeout_ms": per_probe_timeout_ms,
            "target_type": target_type,
            "probe_count": probe_count,
            "interval_ms": interval_ms,
            **kwargs,
        })
        if self.raise_exc_for and self.raise_exc_for == target_ip:
            raise RuntimeError(f"Simulated crash probing {target_ip}")

        if target_ip in self.per_target_results:
            return self.per_target_results[target_ip]

        probes = [
            PingProbeObservation(
                sample_index=i + 1,
                timestamp_utc="2026-10-22T20:00:00Z",
                status=MeasurementStatus.SUCCESS if self.default_status == Status.SUCCESS else MeasurementStatus.FAILED,
                rtt_ms=rtt if self.default_status == Status.SUCCESS else None,
            )
            for i, rtt in enumerate(self.rtts)
        ]
        return PingMeasurementResult(
            target_ip=target_ip,
            ip_family="IPv4",
            probe_count_configured=len(self.rtts),
            probe_count_attempted=len(self.rtts),
            probe_count_successful=len(self.rtts) if self.default_status == Status.SUCCESS else 0,
            probe_count_failed=0 if self.default_status == Status.SUCCESS else len(self.rtts),
            packet_loss_pct=self.loss_pct if self.default_status == Status.SUCCESS else 100.0,
            individual_rtts=self.rtts if self.default_status == Status.SUCCESS else [],
            status=self.default_status,
            probes=probes,
        )


class MockDNSMeasurementAdapter(DNSMeasurementAdapter):
    def __init__(
        self,
        default_status: Status = Status.SUCCESS,
        elapsed_ms: float = 12.5,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        super().__init__()
        self.default_status = default_status
        self.elapsed_ms = elapsed_ms
        self.raise_exc = raise_exc
        self.execute_calls: List[Dict[str, Any]] = []

    def execute(
        self,
        resolver_ip: str,
        domain: str,
        record_type: str = "A",
        port: int = 53,
        timeout_s: Optional[float] = None,
        resolver_id: Optional[str] = None,
        **kwargs: Any,
    ) -> DNSMeasurementResult:
        self.execute_calls.append({
            "resolver_ip": resolver_ip,
            "domain": domain,
            "record_type": record_type,
            "port": port,
            "timeout_s": timeout_s,
            "resolver_id": resolver_id,
            **kwargs,
        })
        if self.raise_exc:
            raise self.raise_exc

        return DNSMeasurementResult(
            resolver_ip=resolver_ip,
            resolver_port=kwargs.get("port", 53),
            ip_family="IPv4",
            query_domain=domain,
            elapsed_ms=self.elapsed_ms if self.default_status == Status.SUCCESS else None,
            rcode=0 if self.default_status == Status.SUCCESS else 2,
            rcode_name="NOERROR" if self.default_status == Status.SUCCESS else "SERVFAIL",
            status=self.default_status,
            resolution_status=DNSResolutionStatus.RESOLVED if self.default_status == Status.SUCCESS else DNSResolutionStatus.SERVFAIL,
            has_answer=self.default_status == Status.SUCCESS,
            answer_count=1 if self.default_status == Status.SUCCESS else 0,
            answer_ips=["93.184.216.34"] if self.default_status == Status.SUCCESS else [],
        )


class MockThroughputAdapter(ThroughputMeasurementAdapter):
    def __init__(
        self,
        status: Status = Status.SUCCESS,
        throughput_mbps: float = 85.5,
        bytes_transferred: int = 15_000_000,
        aborted_flag: bool = False,
    ) -> None:
        super().__init__()
        self.status = status
        self.throughput_mbps = throughput_mbps
        self.bytes_transferred = bytes_transferred
        self.aborted = aborted_flag
        self.execute_calls: List[Dict[str, Any]] = []

    def abort(self) -> None:
        self.aborted = True

    def execute(
        self,
        endpoint_url: str,
        cancel_event: Optional[threading.Event] = None,
        on_chunk_received: Optional[Callable[[int, int], None]] = None,
        **kwargs: Any,
    ) -> ThroughputMeasurementResult:
        self.execute_calls.append({
            "endpoint_url": endpoint_url,
            "cancel_event": cancel_event,
            "on_chunk_received": on_chunk_received,
            **kwargs,
        })
        if cancel_event and cancel_event.is_set():
            return ThroughputMeasurementResult(
                target_url=endpoint_url,
                status=Status.CANCELLED,
                error_message="Cancelled by user",
            )
        return ThroughputMeasurementResult(
            target_url=endpoint_url,
            status=self.status,
            bytes_transferred=self.bytes_transferred if self.status == Status.SUCCESS else 0,
            elapsed_s=2.0,
            throughput_bps=self.throughput_mbps * 1_000_000.0 if self.status == Status.SUCCESS else None,
            throughput_mbps=self.throughput_mbps if self.status == Status.SUCCESS else None,
        )


class MockLoadedLatencyAdapter(LoadedLatencyAdapter):
    def __init__(
        self,
        status: Status = Status.SUCCESS,
        delta_rtt_median_ms: float = 25.0,
        bufferbloat_ratio: float = 3.0,
    ) -> None:
        super().__init__()
        self.status = status
        self.delta_rtt_median_ms = delta_rtt_median_ms
        self.bufferbloat_ratio = bufferbloat_ratio
        self.execute_calls: List[Dict[str, Any]] = []

    def execute(
        self,
        target_ip: str,
        traffic_url: str,
        cancel_event: Optional[threading.Event] = None,
        **kwargs: Any,
    ) -> LoadedLatencyMeasurementResult:
        self.execute_calls.append({
            "target_ip": target_ip,
            "traffic_url": traffic_url,
            "cancel_event": cancel_event,
            **kwargs,
        })
        if cancel_event and cancel_event.is_set():
            return LoadedLatencyMeasurementResult(
                target_ip=target_ip,
                traffic_url=traffic_url,
                status=Status.CANCELLED,
                error_message="Cancelled by user",
            )
        return LoadedLatencyMeasurementResult(
            target_ip=target_ip,
            traffic_url=traffic_url,
            status=self.status,
            baseline_sample_count=5,
            baseline_valid_count=5,
            baseline_median_rtt_ms=10.0,
            loaded_sample_count=5,
            loaded_valid_count=5,
            loaded_median_rtt_ms=35.0,
            delta_rtt_median_ms=self.delta_rtt_median_ms if self.status == Status.SUCCESS else None,
            bufferbloat_ratio=self.bufferbloat_ratio if self.status == Status.SUCCESS else None,
            delta_pct=250.0 if self.status == Status.SUCCESS else None,
            load_active_during_probes=True if self.status == Status.SUCCESS else False,
            load_bytes_transferred=5_000_000 if self.status == Status.SUCCESS else 0,
            load_throughput_mbps=40.0 if self.status == Status.SUCCESS else None,
        )


MockPingAdapter = MockPingMeasurementAdapter


@pytest.fixture
def in_memory_db(tmp_path):
    db_file = tmp_path / "test_orch.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def default_config():
    return load_app_config()


# ============================================================================
# Section 1: Normal Run Lifecycle & Sequencing
# ============================================================================

class TestWorkflowNormalExecution:
    """Verifies standard 5-stage sequential execution."""

    def test_successful_full_five_stage_run(self, in_memory_db, default_config):
        mock_gw = MockGatewayDiscoveryAdapter()
        mock_ping = MockPingAdapter()
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()
        event_q = queue.Queue()

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
            event_queue=event_q,
        )

        run = orchestrator.execute_run(config=default_config)

        # 1. Assert TestRun status
        assert run.status == RunStatus.COMPLETED
        assert run.overall_error is None
        assert run.gateway_ip == "192.168.1.1"
        assert run.interface_name == "eth0"

        # 2. Assert stages in SQLite
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert len(stages) == 5
        assert [s.stage_order for s in stages] == [1, 2, 3, 4, 5]
        assert [s.stage_name for s in stages] == [
            "Gateway Discovery",
            "Gateway Reachability",
            "External Targets Probe",
            "DNS Resolution",
            "Throughput & Loaded Latency",
        ]
        assert all(s.status == StageStatus.SUCCESS for s in stages)

        # 3. Assert observations exist in database
        obs_repo = ObservationRepository(in_memory_db)
        observations = obs_repo.get_observations_for_run(run.run_id)
        assert len(observations) > 0

        # Check gateway discovery observation
        gw_obs = [o for o in observations if o.metric_name == "gateway_discovery"]
        assert len(gw_obs) == 1
        assert gw_obs[0].value_numeric == 1.0

        # Check ICMP observations
        ping_obs = [o for o in observations if o.metric_name == "round_trip_time"]
        assert len(ping_obs) >= 4

        # Check DNS observations
        dns_obs = [o for o in observations if o.metric_name == "dns_query_time"]
        assert len(dns_obs) >= 2

        # Check loaded latency observations
        load_obs = [o for o in observations if o.metric_name == "loaded_latency_delta"]
        assert len(load_obs) == 1

        # 4. Assert events emitted to queue
        events: List[WorkflowEvent] = []
        while not event_q.empty():
            events.append(event_q.get_nowait())

        event_types = [e.event_type for e in events]
        assert WorkflowEventType.RUN_STARTED in event_types
        assert WorkflowEventType.RUN_COMPLETED in event_types
        assert event_types.count(WorkflowEventType.STAGE_STARTED) == 5
        assert event_types.count(WorkflowEventType.STAGE_COMPLETED) == 5

    def test_throughput_only_when_loaded_latency_disabled(self, in_memory_db, default_config):
        # Disable loaded latency in config
        targets_cfg = dataclasses.replace(
            default_config.targets,
            loaded_latency=dataclasses.replace(default_config.targets.loaded_latency, enabled=False),
        )
        cfg = dataclasses.replace(default_config, targets=targets_cfg)

        mock_gw = MockGatewayDiscoveryAdapter()
        mock_ping = MockPingAdapter()
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
        )

        run = orchestrator.execute_run(config=cfg)
        assert run.status == RunStatus.COMPLETED

        # Throughput adapter must have been called, loaded latency must NOT have been called
        assert len(mock_tp.execute_calls) == 1
        assert len(mock_load.execute_calls) == 0


# ============================================================================
# Section 2: Skip Logic (Gateway Unreachability per SRS-F-008)
# ============================================================================

class TestWorkflowSkipLogic:
    """Verifies that downstream stages are skipped when critical dependencies fail."""

    def test_gateway_discovery_failure_skips_throughput(self, in_memory_db, default_config):
        mock_gw = MockGatewayDiscoveryAdapter(status=Status.FAILED, error_message="No active routing interface")
        mock_ping = MockPingAdapter()
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
        )

        run = orchestrator.execute_run(config=default_config)

        # Run should be PARTIAL because external ping and public DNS still ran and succeeded
        assert run.status == RunStatus.PARTIAL

        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert stages[0].status == StageStatus.FAILED  # Stage 1: Discovery Failed
        assert stages[1].status == StageStatus.SKIPPED  # Stage 2: Gateway ping skipped (no IP)
        assert stages[2].status == StageStatus.SUCCESS  # Stage 3: External targets succeeded
        assert stages[3].status == StageStatus.SUCCESS  # Stage 4: Public DNS succeeded
        assert stages[4].status == StageStatus.SKIPPED  # Stage 5: Throughput skipped!
        assert "Gateway unreachable" in (stages[4].skip_reason or "")

        # Bandwidth-intensive load must NOT have been dispatched
        assert len(mock_tp.execute_calls) == 0
        assert len(mock_load.execute_calls) == 0

    def test_gateway_ping_100_percent_loss_skips_throughput(self, in_memory_db, default_config):
        mock_gw = MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1")
        # Gateway returns 100% loss (unreachable)
        mock_ping = MockPingAdapter(
            per_target_results={
                "192.168.1.1": PingMeasurementResult(
                    target_ip="192.168.1.1",
                    ip_family="IPv4",
                    probe_count_attempted=4,
                    probe_count_successful=0,
                    probe_count_failed=4,
                    packet_loss_pct=100.0,
                    status=Status.SUCCESS,
                )
            }
        )
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
        )

        run = orchestrator.execute_run(config=default_config)

        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert stages[0].status == StageStatus.SUCCESS  # Discovery OK
        assert stages[1].status == StageStatus.SUCCESS  # Ping measurement ran
        assert stages[4].status == StageStatus.SKIPPED  # Throughput stage skipped per policy
        assert "Gateway unreachable" in (stages[4].skip_reason or "")
        assert len(mock_load.execute_calls) == 0


# ============================================================================
# Section 3: Cancellation & State Preservation (SRS-F-020, TC-INT-03)
# ============================================================================

class TestWorkflowCancellation:
    """Verifies graceful cancellation, probe abort, and state preservation."""

    def test_cancellation_before_execution_starts(self, in_memory_db, default_config):
        cancel_ev = threading.Event()
        cancel_ev.set()  # Pre-cancelled

        mock_gw = MockGatewayDiscoveryAdapter()
        orchestrator = WorkflowOrchestrator(db=in_memory_db, gateway_adapter=mock_gw)

        run = orchestrator.execute_run(config=default_config, cancel_event=cancel_ev)
        assert run.status == RunStatus.CANCELLED
        assert "cancelled before execution" in (run.overall_error or "")
        assert len(mock_gw.execute_calls) == 0

    def test_cancellation_during_stage_3_external_probing(self, in_memory_db, default_config):
        cancel_ev = threading.Event()
        mock_gw = MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1")

        # Simulate cancellation triggering inside stage 3
        mock_ping = MockPingAdapter()
        orig_execute = mock_ping.execute
        def cancel_on_first_external(
            target_ip: str,
            count: int = 4,
            per_probe_timeout_ms: int = 1000,
            target_type: TargetType = TargetType.GATEWAY,
            probe_count: Optional[int] = None,
            interval_ms: Optional[int] = None,
            **kwargs: Any,
        ) -> PingMeasurementResult:
            if target_ip != "192.168.1.1":
                cancel_ev.set()
            return orig_execute(
                target_ip,
                count=count,
                per_probe_timeout_ms=per_probe_timeout_ms,
                target_type=target_type,
                probe_count=probe_count,
                interval_ms=interval_ms,
                **kwargs,
            )

        mock_ping.execute = cancel_on_first_external
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
        )

        run = orchestrator.execute_run(config=default_config, cancel_event=cancel_ev)

        assert run.status == RunStatus.CANCELLED
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)

        # Stage 1 and 2 must be complete
        assert stages[0].status == StageStatus.SUCCESS
        assert stages[1].status == StageStatus.SUCCESS
        # Stage 3 cancelled
        assert stages[2].status == StageStatus.CANCELLED
        # Downstream stages 4 and 5 must NOT have run
        assert len(stages) == 3

        # State Preservation Guarantee (TC-INT-03 / AC-PERS-02):
        # Verify earlier observations from stages 1 and 2 remain safely in SQLite
        obs_repo = ObservationRepository(in_memory_db)
        saved_obs = obs_repo.get_observations_for_run(run.run_id)
        assert len(saved_obs) > 0
        gw_probe_obs = [o for o in saved_obs if o.stage_id == stages[1].stage_id]
        assert len(gw_probe_obs) > 0

    def test_abort_active_operations_calls_throughput_abort(self, in_memory_db):
        mock_tp = MockThroughputAdapter()
        orchestrator = WorkflowOrchestrator(db=in_memory_db, throughput_adapter=mock_tp)

        assert not mock_tp.aborted
        orchestrator.abort_active_operations()
        assert mock_tp.aborted


# ============================================================================
# Section 4: Configuration Validation & Error Resilience
# ============================================================================

class TestWorkflowValidationAndErrors:
    """Verifies pre-flight validation and exception safety."""

    def test_invalid_config_type_fails_before_execution(self, in_memory_db):
        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        # Pass non-AppConfig
        run = orchestrator.execute_run(config="invalid_config_string")  # type: ignore

        assert run.status == RunStatus.FAILED
        assert "expected AppConfig" in (run.overall_error or "")
        # Zero stages created
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert len(stages) == 0

    def test_config_missing_external_targets_fails(self, in_memory_db, default_config):
        # Empty external targets list
        targets_cfg = dataclasses.replace(
            default_config.targets,
            external_targets=dataclasses.replace(default_config.targets.external_targets, targets=[]),
        )
        cfg = dataclasses.replace(default_config, targets=targets_cfg)

        orchestrator = WorkflowOrchestrator(db=in_memory_db)
        run = orchestrator.execute_run(config=cfg)

        assert run.status == RunStatus.FAILED
        assert "minimum 2 external targets" in (run.overall_error or "")

    def test_unexpected_adapter_exception_captured_cleanly(self, in_memory_db, default_config):
        mock_gw = MockGatewayDiscoveryAdapter(raise_exc=RuntimeError("Subprocess kernel deadlock"))
        mock_ping = MockPingAdapter()
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
        )

        run = orchestrator.execute_run(config=default_config)

        # Stage 1 caught exception, marked FAILED
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert stages[0].status == StageStatus.FAILED
        assert "Subprocess kernel deadlock" in (stages[0].error_message or "")

    def test_event_queue_full_drops_silently(self, in_memory_db, default_config):
        # Event queue with maxsize=1 should drop events when full without crashing
        tiny_q = queue.Queue(maxsize=1)
        tiny_q.put(WorkflowEvent(WorkflowEventType.LOG, "pre-filled", "2026-10-22T20:00:00Z"))

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
            load_adapter=MockLoadedLatencyAdapter(),
            event_queue=tiny_q,
        )

        run = orchestrator.execute_run(config=default_config)
        assert run.status == RunStatus.COMPLETED

    def test_on_event_callback_exception_handled_cleanly(self, in_memory_db, default_config):
        def crashing_callback(evt):
            raise ValueError("UI callback crashed")

        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
            load_adapter=MockLoadedLatencyAdapter(),
            on_event=crashing_callback,
        )

        run = orchestrator.execute_run(config=default_config)
        assert run.status == RunStatus.COMPLETED

    def test_stage2_gateway_probe_failure_and_exception(self, in_memory_db, default_config):
        # Case A: Gateway ping returns FAILED status
        mock_ping_fail = MockPingAdapter(default_status=Status.FAILED)
        orch1 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=mock_ping_fail,
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
        )
        run1 = orch1.execute_run(config=default_config)
        stages1 = orch1.run_repo.get_stages_for_run(run1.run_id)
        assert stages1[1].status == StageStatus.FAILED
        assert stages1[4].status == StageStatus.SKIPPED  # Stage 5 skipped due to gateway unreachability

        # Case B: Gateway ping raises exception
        mock_ping_exc = MockPingAdapter(raise_exc_for="192.168.1.1")
        orch2 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=mock_ping_exc,
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
        )
        run2 = orch2.execute_run(config=default_config)
        stages2 = orch2.run_repo.get_stages_for_run(run2.run_id)
        assert stages2[1].status == StageStatus.FAILED
        assert "Simulated crash probing 192.168.1.1" in (stages2[1].error_message or "")

    def test_stage3_all_external_targets_fail(self, in_memory_db, default_config):
        # All external targets return FAILED
        mock_ping = MockPingAdapter()
        def fail_externals(
            target_ip: str,
            count: int = 4,
            per_probe_timeout_ms: int = 1000,
            target_type: TargetType = TargetType.GATEWAY,
            probe_count: Optional[int] = None,
            interval_ms: Optional[int] = None,
            **kwargs: Any,
        ) -> PingMeasurementResult:
            if target_ip == "192.168.1.1":
                return MockPingAdapter().execute(
                    target_ip,
                    count=count,
                    per_probe_timeout_ms=per_probe_timeout_ms,
                    target_type=target_type,
                    probe_count=probe_count,
                    interval_ms=interval_ms,
                    **kwargs,
                )
            return PingMeasurementResult(
                target_ip=target_ip,
                ip_family="IPv4",
                status=Status.FAILED,
                error_message="Network unreachable",
            )

        mock_ping.execute = fail_externals
        orchestrator = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=mock_ping,
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
        )

        run = orchestrator.execute_run(config=default_config)
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert stages[2].status == StageStatus.FAILED
        assert "All external targets failed" in (stages[2].error_message or "")

    def test_stage4_dns_all_queries_fail_and_exception(self, in_memory_db, default_config):
        # Case A: DNS returns FAILED status
        mock_dns_fail = MockDNSMeasurementAdapter(default_status=Status.FAILED)
        orch1 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=MockPingAdapter(),
            dns_adapter=mock_dns_fail,
            throughput_adapter=MockThroughputAdapter(),
        )
        run1 = orch1.execute_run(config=default_config)
        stages1 = orch1.run_repo.get_stages_for_run(run1.run_id)
        assert stages1[3].status == StageStatus.FAILED

        # Case B: DNS raises exception
        mock_dns_exc = MockDNSMeasurementAdapter(raise_exc=RuntimeError("DNS socket buffer error"))
        orch2 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=MockPingAdapter(),
            dns_adapter=mock_dns_exc,
            throughput_adapter=MockThroughputAdapter(),
        )
        run2 = orch2.execute_run(config=default_config)
        stages2 = orch2.run_repo.get_stages_for_run(run2.run_id)
        assert stages2[3].status == StageStatus.FAILED
        assert "DNS socket buffer error" in (stages2[3].error_message or "")

    def test_stage5_throughput_and_loaded_latency_failure_outcomes(self, in_memory_db, default_config):
        # Case A: Throughput adapter returns FAILED
        mock_tp_fail = MockThroughputAdapter(status=Status.FAILED)
        targets_no_load = dataclasses.replace(
            default_config.targets,
            loaded_latency=dataclasses.replace(default_config.targets.loaded_latency, enabled=False),
        )
        cfg_tp = dataclasses.replace(default_config, targets=targets_no_load)

        orch1 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=mock_tp_fail,
        )
        run1 = orch1.execute_run(config=cfg_tp)
        stages1 = orch1.run_repo.get_stages_for_run(run1.run_id)
        assert stages1[4].status == StageStatus.FAILED

        # Case B: Loaded latency adapter returns FAILED
        mock_load_fail = MockLoadedLatencyAdapter(status=Status.FAILED)
        orch2 = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(gateway_ip="192.168.1.1"),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            load_adapter=mock_load_fail,
        )
        run2 = orch2.execute_run(config=default_config)
        stages2 = orch2.run_repo.get_stages_for_run(run2.run_id)
        assert stages2[4].status == StageStatus.FAILED

    def test_abort_active_operations_handles_adapter_exceptions(self, in_memory_db):
        class CrashingThroughputAdapter(MockThroughputAdapter):
            def abort(self):
                raise RuntimeError("Socket already disconnected")

        orch = WorkflowOrchestrator(db=in_memory_db, throughput_adapter=CrashingThroughputAdapter())
        # Must not raise
        orch.abort_active_operations()

    def test_run_finalization_database_commit_failure_marks_run_failed(self, in_memory_db, default_config):
        """Verify that a database commit failure during run finalization marks run as FAILED."""
        event_q = queue.Queue()
        orch = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
            load_adapter=MockLoadedLatencyAdapter(),
            event_queue=event_q,
        )

        real_update_run = orch.run_repo.update_run
        call_count = 0

        def failing_update_run(run):
            nonlocal call_count
            call_count += 1
            # Allow stage updates, but fail the final update_run call in _finalize_run
            if run.status in (RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.CANCELLED) or call_count > 2:
                raise sqlite3.OperationalError("Simulated disk I/O error during final run update")
            return real_update_run(run)

        orch.run_repo.update_run = failing_update_run

        run = orch.execute_run(config=default_config)

        # Invariant: Must NOT be marked COMPLETED when persistence commit fails
        assert run.status == RunStatus.FAILED
        assert "Database commit failure during finalization" in (run.overall_error or "")

        events = []
        while not event_q.empty():
            events.append(event_q.get_nowait())
        run_events = [e for e in events if e.event_type == WorkflowEventType.RUN_FAILED]
        assert len(run_events) >= 1
        assert run_events[-1].status == RunStatus.FAILED.value

    def test_run_finalization_stage_query_failure_marks_run_failed(self, in_memory_db, default_config):
        """Verify that a database query error during stage inspection marks run as FAILED."""
        orch = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
            load_adapter=MockLoadedLatencyAdapter(),
        )

        def failing_get_stages(run_id):
            raise sqlite3.OperationalError("Simulated database corruption reading stages")

        orch.run_repo.get_stages_for_run = failing_get_stages

        run = orch.execute_run(config=default_config)

        # Invariant: Must NOT be marked COMPLETED when stages cannot be queried
        assert run.status == RunStatus.FAILED
        assert "Database read failure during finalization" in (run.overall_error or "")

    def test_stage_observation_persistence_failure_marks_stage_failed(self, in_memory_db, default_config):
        """Verify that failure to persist stage observations marks stage as FAILED and run as PARTIAL/FAILED."""
        orch = WorkflowOrchestrator(
            db=in_memory_db,
            gateway_adapter=MockGatewayDiscoveryAdapter(),
            ping_adapter=MockPingAdapter(),
            dns_adapter=MockDNSMeasurementAdapter(),
            throughput_adapter=MockThroughputAdapter(),
            load_adapter=MockLoadedLatencyAdapter(),
        )

        def failing_create_observations_batch(observations: list[Observation]) -> list[Observation]:
            raise sqlite3.OperationalError("Simulated disk full writing observations")

        orch.obs_repo.create_observations_batch = failing_create_observations_batch

        run = orch.execute_run(config=default_config)

        # Invariant: Must NOT be COMPLETED when observations could not be persisted
        assert run.status in (RunStatus.PARTIAL, RunStatus.FAILED)
        assert run.status != RunStatus.COMPLETED

