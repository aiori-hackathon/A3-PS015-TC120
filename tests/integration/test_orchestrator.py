"""Integration tests for Staged Workflow Orchestration (Phase 7).

Verifies:
- TC-INT-02: Staged workflow progression (1->2->3->4->5) and skip logic on Gateway failure.
- TC-INT-03: Cancellation handling during Stage 3, in-flight abort, state preservation in SQLite.
- Full end-to-end SQLite persistence integrity across all stages.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Optional
import pytest

from ticnet.configuration.loader import load_app_config
from ticnet.measurements.ping.models import PingMeasurementResult
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
from tests.unit.test_orchestrator import (
    MockDNSMeasurementAdapter,
    MockGatewayDiscoveryAdapter,
    MockLoadedLatencyAdapter,
    MockPingMeasurementAdapter,
    MockThroughputAdapter,
)


@pytest.fixture
def integration_db(tmp_path):
    db_file = tmp_path / "integration_ticnet.db"
    db = DatabaseManager(str(db_file))
    db.run_migrations()
    return db


@pytest.fixture
def config():
    return load_app_config()


class TestTCINT02StagedWorkflowExecution:
    """Verifies TC-INT-02: Staged workflow execution (1->2->3->4->5) and skip logic."""

    def test_tc_int_02_full_stage_progression_healthy_network(self, integration_db, config):
        """Verify normal sequential progression of stages 1 through 5."""
        mock_gw = MockGatewayDiscoveryAdapter(
            status=Status.SUCCESS,
            gateway_ip="192.168.1.1",
            interface_name="Wi-Fi",
        )
        mock_ping = MockPingMeasurementAdapter(default_status=Status.SUCCESS, loss_pct=0.0)
        mock_dns = MockDNSMeasurementAdapter(default_status=Status.SUCCESS)
        mock_tp = MockThroughputAdapter(status=Status.SUCCESS)
        mock_load = MockLoadedLatencyAdapter(status=Status.SUCCESS)
        event_q = queue.Queue()

        orchestrator = WorkflowOrchestrator(
            db=integration_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
            event_queue=event_q,
        )

        run = orchestrator.execute_run(config=config)

        # 1. Assert Run is COMPLETED
        assert run.status == RunStatus.COMPLETED
        assert run.gateway_ip == "192.168.1.1"
        assert run.interface_name == "Wi-Fi"
        assert run.end_time_utc is not None

        # 2. Assert all 5 stages ran in exact order and all succeeded
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

        # 3. Assert observations and summaries in SQLite
        obs_repo = ObservationRepository(integration_db)
        sum_repo = SummaryRepository(integration_db)

        obs = obs_repo.get_observations_for_run(run.run_id)
        summaries = sum_repo.get_summaries_for_run(run.run_id)

        assert len(obs) > 0
        assert len(summaries) > 0

        # Assert metrics across all stages are present
        metric_names = {o.metric_name for o in obs}
        assert "gateway_discovery" in metric_names
        assert "round_trip_time" in metric_names
        assert "dns_query_time" in metric_names
        assert "loaded_latency_delta" in metric_names

    def test_tc_int_02_gateway_failure_skips_throughput_stage(self, integration_db, config):
        """Simulate Gateway Failure and verify Throughput stage is marked SKIPPED per SRS-F-008."""
        # Stage 1: Discovery fails completely
        mock_gw = MockGatewayDiscoveryAdapter(
            status=Status.FAILED,
            error_message="Default gateway not found in OS route table",
        )
        mock_ping = MockPingMeasurementAdapter(default_status=Status.SUCCESS)
        mock_dns = MockDNSMeasurementAdapter(default_status=Status.SUCCESS)
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()
        event_q = queue.Queue()

        orchestrator = WorkflowOrchestrator(
            db=integration_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
            event_queue=event_q,
        )

        run = orchestrator.execute_run(config=config)

        # 1. Assert Run is PARTIAL (external and DNS succeeded, but gateway failed and throughput was skipped)
        assert run.status == RunStatus.PARTIAL

        # 2. Assert Stage 5 (Throughput & Loaded Latency) is explicitly SKIPPED
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert len(stages) == 5

        s1, s2, s3, s4, s5 = stages
        assert s1.status == StageStatus.FAILED
        assert s2.status == StageStatus.SKIPPED  # No gateway IP to ping
        assert s3.status == StageStatus.SUCCESS  # External ping succeeded
        assert s4.status == StageStatus.SUCCESS  # DNS succeeded
        assert s5.status == StageStatus.SKIPPED  # Downstream bandwidth-intensive stage skipped!

        assert "Gateway unreachable" in (s5.skip_reason or "")
        assert "SRS-F-008" in (s5.skip_reason or "")

        # 3. Assert no throughput or load adapter calls were made
        assert len(mock_tp.execute_calls) == 0
        assert len(mock_load.execute_calls) == 0


class TestTCINT03CancellationHandling:
    """Verifies TC-INT-03: Cancellation during Stage 3, in-flight abort, state preservation."""

    def test_tc_int_03_cancellation_during_stage_3_preserves_earlier_observations(
        self, integration_db, config
    ):
        """Signal cancellation during Stage 3.

        Verify:
        - In-flight probe aborts
        - Stage 3 marked CANCELLED
        - Run marked CANCELLED
        - Downstream stages 4 and 5 not executed
        - Earlier observations from Stages 1 and 2 remain in database
        """
        cancel_event = threading.Event()
        mock_gw = MockGatewayDiscoveryAdapter(
            status=Status.SUCCESS,
            gateway_ip="192.168.1.1",
            interface_name="eth0",
        )

        # Trigger cancellation on the first external probe call in Stage 3
        mock_ping = MockPingMeasurementAdapter()
        orig_execute = mock_ping.execute

        def cancel_on_external(
            target_ip: str,
            count: int = 4,
            per_probe_timeout_ms: int = 1000,
            target_type: TargetType = TargetType.GATEWAY,
            probe_count: Optional[int] = None,
            interval_ms: Optional[int] = None,
            **kwargs: Any,
        ) -> PingMeasurementResult:
            if target_ip != "192.168.1.1":
                cancel_event.set()
            return orig_execute(
                target_ip,
                count=count,
                per_probe_timeout_ms=per_probe_timeout_ms,
                target_type=target_type,
                probe_count=probe_count,
                interval_ms=interval_ms,
                **kwargs,
            )

        mock_ping.execute = cancel_on_external
        mock_dns = MockDNSMeasurementAdapter()
        mock_tp = MockThroughputAdapter()
        mock_load = MockLoadedLatencyAdapter()
        event_q = queue.Queue()

        orchestrator = WorkflowOrchestrator(
            db=integration_db,
            gateway_adapter=mock_gw,
            ping_adapter=mock_ping,
            dns_adapter=mock_dns,
            throughput_adapter=mock_tp,
            load_adapter=mock_load,
            event_queue=event_q,
        )

        run = orchestrator.execute_run(config=config, cancel_event=cancel_event)

        # 1. Assert Run is CANCELLED
        assert run.status == RunStatus.CANCELLED
        assert "cancelled" in (run.overall_error or "").lower()

        # 2. Assert stages status
        stages = orchestrator.run_repo.get_stages_for_run(run.run_id)
        assert len(stages) == 3  # Only stages 1, 2, and 3 were created

        s1, s2, s3 = stages
        assert s1.status == StageStatus.SUCCESS
        assert s2.status == StageStatus.SUCCESS
        assert s3.status == StageStatus.CANCELLED

        # 3. Assert earlier observations from Stage 1 & Stage 2 are safely committed in SQLite
        obs_repo = ObservationRepository(integration_db)
        saved_obs = obs_repo.get_observations_for_run(run.run_id)
        assert len(saved_obs) > 0

        # Stage 1 discovery observation preserved
        s1_obs = [o for o in saved_obs if o.stage_id == s1.stage_id]
        assert len(s1_obs) == 1
        assert s1_obs[0].metric_name == "gateway_discovery"
        assert s1_obs[0].status == MeasurementStatus.SUCCESS

        # Stage 2 ping observations preserved
        s2_obs = [o for o in saved_obs if o.stage_id == s2.stage_id]
        assert len(s2_obs) >= 4
        assert all(o.metric_name == "round_trip_time" for o in s2_obs)

        # Stage 2 summaries preserved
        sum_repo = SummaryRepository(integration_db)
        saved_sums = sum_repo.get_summaries_for_run(run.run_id)
        assert len(saved_sums) > 0

        # 4. Assert Stage 4 and 5 were never invoked
        assert len(mock_dns.execute_calls) == 0
        assert len(mock_tp.execute_calls) == 0
        assert len(mock_load.execute_calls) == 0
