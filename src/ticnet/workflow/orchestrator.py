"""Staged workflow orchestrator for TicNet (Phase 7 - SRS-F-008, SRS-F-020).

Sequentially coordinates and executes measurement adapters:
- Stage 1: Network & Default Gateway Discovery (GatewayDiscoveryAdapter)
- Stage 2: Gateway Reachability and Delay Probing (PingMeasurementAdapter)
- Stage 3: Multi-Target External Probing (PingMeasurementAdapter)
- Stage 4: DNS Resolution Timing and Status (DNSMeasurementAdapter)
- Stage 5: Throughput & Loaded Latency (ThroughputMeasurementAdapter / LoadedLatencyAdapter)

Key Guarantees:
- Deterministic sequential execution and explicit run lifecycle.
- Pre-flight configuration validation before any measurement starts.
- Staged skip policy: Downstream bandwidth-intensive stages (Stage 5) are skipped
  if the gateway is unreachable per SRS-F-008.
- Immediate cancellation responsiveness: Checks cancel_event across stage boundaries
  and probe iterations; aborts in-flight throughput; commits prior observations.
- Atomic persistence of stage results and raw observations to SQLite.
- Structured logging with privacy masking.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from ticnet.configuration.validator import (
    AppConfig,
    ConfigValidationError,
    ConfigValidator,
)
from ticnet.measurements.base import GatewayDiscoveryResult
from ticnet.measurements.dns.adapter import DNSMeasurementAdapter
from ticnet.measurements.dns.models import DNSMeasurementResult
from ticnet.measurements.gateway.discovery import GatewayDiscoveryAdapter
from ticnet.measurements.load.adapter import LoadedLatencyAdapter
from ticnet.measurements.load.models import LoadedLatencyMeasurementResult
from ticnet.measurements.ping.adapter import PingMeasurementAdapter
from ticnet.measurements.ping.models import PingMeasurementResult
from ticnet.measurements.throughput.adapter import ThroughputMeasurementAdapter
from ticnet.measurements.throughput.models import ThroughputMeasurementResult
from ticnet.models.entities import (
    Observation,
    Target,
    TestRun,
    TestStage,
    new_id,
    now_utc_iso,
)
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
from ticnet.utils.logging import get_logger, mask_public_ip_address
from ticnet.workflow.events import WorkflowEvent, WorkflowEventType

logger = get_logger("workflow.orchestrator")


class WorkflowOrchestrator:
    """Orchestrates multi-stage diagnostic measurement workflows."""

    def __init__(
        self,
        db: DatabaseManager,
        gateway_adapter: Optional[GatewayDiscoveryAdapter] = None,
        ping_adapter: Optional[PingMeasurementAdapter] = None,
        dns_adapter: Optional[DNSMeasurementAdapter] = None,
        throughput_adapter: Optional[ThroughputMeasurementAdapter] = None,
        load_adapter: Optional[LoadedLatencyAdapter] = None,
        event_queue: Optional[queue.Queue] = None,
        on_event: Optional[Callable[[WorkflowEvent], None]] = None,
        app_version: str = "1.0.0",
        config_version: str = "1.0.0",
    ) -> None:
        """Initialize the orchestrator with required database and optional adapter overrides."""
        self.db = db
        self.run_repo = RunRepository(db)
        self.obs_repo = ObservationRepository(db)
        self.summary_repo = SummaryRepository(db)

        self.gateway_adapter = gateway_adapter or GatewayDiscoveryAdapter()
        self.ping_adapter = ping_adapter or PingMeasurementAdapter()
        self.dns_adapter = dns_adapter or DNSMeasurementAdapter()
        self.throughput_adapter = throughput_adapter or ThroughputMeasurementAdapter()
        self.load_adapter = load_adapter or LoadedLatencyAdapter(
            ping_adapter=self.ping_adapter,
            throughput_adapter=self.throughput_adapter,
        )

        self.event_queue = event_queue
        self.on_event = on_event
        self.app_version = app_version
        self.config_version = config_version

        self._active_cancel_event: Optional[threading.Event] = None

    def emit_event(self, event: WorkflowEvent) -> None:
        """Emit a workflow event to registered subscribers and bounded queue."""
        if self.event_queue is not None:
            try:
                self.event_queue.put_nowait(event)
            except queue.Full:
                logger.warning("Event queue full; dropping event: %s", event.event_type.value)

        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception as cb_err:
                logger.error("Error in on_event callback: %s", cb_err)

    def abort_active_operations(self) -> None:
        """Forcibly abort active network adapters if supported."""
        if hasattr(self.throughput_adapter, "abort"):
            try:
                self.throughput_adapter.abort()
            except Exception as e:
                logger.warning("Error aborting throughput adapter: %s", e)

        if hasattr(self.load_adapter, "throughput_adapter") and hasattr(
            self.load_adapter.throughput_adapter, "abort"
        ):
            try:
                self.load_adapter.throughput_adapter.abort()
            except Exception as e:
                logger.warning("Error aborting loaded latency throughput adapter: %s", e)

    def execute_run(
        self,
        config: AppConfig,
        run_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        run_mode: RunMode = RunMode.OPERATIONAL,
    ) -> TestRun:
        """Execute a complete 5-stage diagnostic test run.

        Stages:
        1. Gateway Discovery
        2. Gateway Reachability (Ping)
        3. External Targets Reachability (Ping)
        4. DNS Resolution (Direct UDP queries)
        5. Throughput & Loaded Latency (Skipped if Gateway is unreachable)

        Args:
            config: Validated AppConfig instance.
            run_id: Optional unique identifier for this run (generated if None).
            cancel_event: Optional threading.Event for external cancellation.
            run_mode: Execution mode (OPERATIONAL or LAB).

        Returns:
            Completed or finalized TestRun domain entity.
        """
        self._active_cancel_event = cancel_event
        actual_run_id = run_id or self.run_repo.generate_next_run_id()
        start_time_iso = now_utc_iso()

        # --------------------------------------------------------------------
        # Pre-Flight Step 1: Configuration Validation
        # --------------------------------------------------------------------
        config_error: Optional[str] = None
        if not isinstance(config, AppConfig):
            config_error = f"Invalid config type: expected AppConfig, got {type(config).__name__}"
        elif not config.targets:
            config_error = "Invalid configuration: targets section is missing"
        elif not config.targets.external_targets or len(config.targets.external_targets.targets) < 2:
            config_error = "Invalid configuration: minimum 2 external targets required"

        if config_error:
            logger.error("Pre-flight configuration validation failed: %s", config_error)
            failed_run = TestRun(
                run_id=actual_run_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                status=RunStatus.FAILED,
                run_mode=run_mode,
                app_version=self.app_version,
                config_version=self.config_version,
                overall_error=config_error,
            )
            try:
                self.run_repo.create_run(failed_run)
            except Exception as db_err:
                logger.error("Failed to record configuration error run in database: %s", db_err)

            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.RUN_FAILED,
                    run_id=actual_run_id,
                    status=RunStatus.FAILED.value,
                    message=config_error,
                )
            )
            return failed_run

        # --------------------------------------------------------------------
        # Pre-Flight Step 2: Early Cancellation Check
        # --------------------------------------------------------------------
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Diagnostic run %s was cancelled before execution started", actual_run_id)
            cancelled_run = TestRun(
                run_id=actual_run_id,
                start_time_utc=start_time_iso,
                end_time_utc=now_utc_iso(),
                status=RunStatus.CANCELLED,
                run_mode=run_mode,
                app_version=self.app_version,
                config_version=self.config_version,
                overall_error="Diagnostic run was cancelled before execution started",
            )
            try:
                self.run_repo.create_run(cancelled_run)
            except Exception as db_err:
                logger.error("Failed to persist early-cancelled run: %s", db_err)

            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.RUN_CANCELLED,
                    run_id=actual_run_id,
                    status=RunStatus.CANCELLED.value,
                    message="Diagnostic run cancelled before execution started",
                )
            )
            return cancelled_run

        # --------------------------------------------------------------------
        # Initialize TestRun in Persistence
        # --------------------------------------------------------------------
        run = TestRun(
            run_id=actual_run_id,
            start_time_utc=start_time_iso,
            status=RunStatus.RUNNING,
            run_mode=run_mode,
            app_version=self.app_version,
            config_version=self.config_version,
        )
        self.run_repo.create_run(run)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.RUN_STARTED,
                run_id=run.run_id,
                status=RunStatus.RUNNING.value,
                message="Diagnostic run started",
            )
        )

        gateway_unreachable: bool = False
        gw_target: Optional[Target] = None

        # ====================================================================
        # STAGE 1: Gateway Discovery
        # ====================================================================
        stage1 = TestStage(
            stage_id=new_id(),
            run_id=run.run_id,
            stage_name="Gateway Discovery",
            stage_order=1,
            start_time_utc=now_utc_iso(),
            status=StageStatus.RUNNING,
        )
        self.run_repo.create_stage(stage1)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.STAGE_STARTED,
                run_id=run.run_id,
                stage_id=stage1.stage_id,
                stage_name=stage1.stage_name,
                stage_order=1,
                status=StageStatus.RUNNING.value,
            )
        )

        try:
            if cancel_event is not None and cancel_event.is_set():
                stage1.status = StageStatus.CANCELLED
                stage1.error_message = "Cancelled before gateway discovery"
                stage1.end_time_utc = now_utc_iso()
                self.run_repo.update_stage(stage1)
                self.emit_event(
                    WorkflowEvent(
                        event_type=WorkflowEventType.STAGE_CANCELLED,
                        run_id=run.run_id,
                        stage_id=stage1.stage_id,
                        stage_name=stage1.stage_name,
                        stage_order=1,
                        status=StageStatus.CANCELLED.value,
                    )
                )
                return self._finalize_run(run, cancel_event)

            logger.info("Stage 1: Discovering default network gateway")
            gw_res: GatewayDiscoveryResult = self.gateway_adapter.execute(
                config=config.targets,
                manual_override_ip=config.targets.gateway.manual_override_ip,
                auto_discover=config.targets.gateway.auto_discover,
            )
            stage1.end_time_utc = now_utc_iso()

            if gw_res.status == Status.SUCCESS and gw_res.gateway_ip:
                stage1.status = StageStatus.SUCCESS
                run.gateway_ip = gw_res.gateway_ip
                run.interface_name = gw_res.interface_name
                run.interface_type = gw_res.ip_family
                self.run_repo.update_run(run)

                gw_target = gw_res.to_target()
                if gw_target:
                    self.obs_repo.create_target(gw_target)

                # Persist stage discovery observation
                obs1 = Observation(
                    observation_id=new_id(),
                    run_id=run.run_id,
                    stage_id=stage1.stage_id,
                    target_id=gw_target.target_id if gw_target else None,
                    sample_index=1,
                    timestamp_utc=gw_res.timestamp_utc,
                    duration_ms=0.0,
                    metric_name="gateway_discovery",
                    value_numeric=1.0,
                    unit="status",
                    status=MeasurementStatus.SUCCESS,
                    method_metadata=json.dumps(
                        {
                            "source": gw_res.discovery_source,
                            "interface": gw_res.interface_name,
                            "gateway_ip": gw_res.gateway_ip,
                        }
                    ),
                )
                self.obs_repo.create_observation(obs1)
                logger.info("Gateway discovered: %s on %s", gw_res.gateway_ip, gw_res.interface_name)
            else:
                stage1.status = StageStatus.FAILED
                stage1.error_message = gw_res.error_message or "Gateway discovery failed"
                gateway_unreachable = True
                logger.warning("Gateway discovery failed: %s", stage1.error_message)

            self.run_repo.update_stage(stage1)
            self.emit_event(
                WorkflowEvent(
                    event_type=(
                        WorkflowEventType.STAGE_COMPLETED
                        if stage1.status == StageStatus.SUCCESS
                        else WorkflowEventType.STAGE_FAILED
                    ),
                    run_id=run.run_id,
                    stage_id=stage1.stage_id,
                    stage_name=stage1.stage_name,
                    stage_order=1,
                    status=stage1.status.value,
                    message=stage1.error_message,
                )
            )

        except Exception as e:
            logger.error("Exception in Gateway Discovery stage: %s", e, exc_info=True)
            stage1.status = StageStatus.FAILED
            stage1.error_message = f"Gateway discovery error: {e}"
            stage1.end_time_utc = now_utc_iso()
            self.run_repo.update_stage(stage1)
            gateway_unreachable = True
            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.STAGE_FAILED,
                    run_id=run.run_id,
                    stage_id=stage1.stage_id,
                    stage_name=stage1.stage_name,
                    stage_order=1,
                    status=StageStatus.FAILED.value,
                    message=stage1.error_message,
                )
            )

        if cancel_event is not None and cancel_event.is_set():
            return self._finalize_run(run, cancel_event)

        # ====================================================================
        # STAGE 2: Gateway Reachability Probing
        # ====================================================================
        stage2 = TestStage(
            stage_id=new_id(),
            run_id=run.run_id,
            stage_name="Gateway Reachability",
            stage_order=2,
            start_time_utc=now_utc_iso(),
            status=StageStatus.RUNNING,
        )
        self.run_repo.create_stage(stage2)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.STAGE_STARTED,
                run_id=run.run_id,
                stage_id=stage2.stage_id,
                stage_name=stage2.stage_name,
                stage_order=2,
                status=StageStatus.RUNNING.value,
            )
        )

        try:
            if cancel_event is not None and cancel_event.is_set():
                stage2.status = StageStatus.CANCELLED
                stage2.error_message = "Cancelled before gateway probing"
                stage2.end_time_utc = now_utc_iso()
                self.run_repo.update_stage(stage2)
                self.emit_event(
                    WorkflowEvent(
                        event_type=WorkflowEventType.STAGE_CANCELLED,
                        run_id=run.run_id,
                        stage_id=stage2.stage_id,
                        stage_name=stage2.stage_name,
                        stage_order=2,
                        status=StageStatus.CANCELLED.value,
                    )
                )
                return self._finalize_run(run, cancel_event)

            if not run.gateway_ip:
                stage2.status = StageStatus.SKIPPED
                stage2.skip_reason = "No gateway IP available from Stage 1"
                stage2.end_time_utc = now_utc_iso()
                gateway_unreachable = True
                self.run_repo.update_stage(stage2)
                self.emit_event(
                    WorkflowEvent(
                        event_type=WorkflowEventType.STAGE_SKIPPED,
                        run_id=run.run_id,
                        stage_id=stage2.stage_id,
                        stage_name=stage2.stage_name,
                        stage_order=2,
                        status=StageStatus.SKIPPED.value,
                        message=stage2.skip_reason,
                    )
                )
            else:
                logger.info("Stage 2: Probing gateway %s", run.gateway_ip)
                ping_res: PingMeasurementResult = self.ping_adapter.execute(
                    target_ip=run.gateway_ip,
                    count=config.targets.gateway.probe_count,
                    per_probe_timeout_ms=config.targets.gateway.probe_timeout_ms,
                    target_type=TargetType.GATEWAY,
                )
                stage2.end_time_utc = now_utc_iso()

                # Persist raw observations and statistical summaries
                obs_list = ping_res.to_observations(
                    run_id=run.run_id,
                    stage_id=stage2.stage_id,
                    target_id=gw_target.target_id if gw_target else None,
                )
                self.obs_repo.create_observations_batch(obs_list)

                sum_list = ping_res.to_summaries(
                    run_id=run.run_id,
                    target_id=gw_target.target_id if gw_target else None,
                )
                self.summary_repo.create_summaries_batch(sum_list)

                if ping_res.status == Status.SUCCESS:
                    stage2.status = StageStatus.SUCCESS
                    if ping_res.packet_loss_pct == 100.0 or ping_res.probe_count_successful == 0:
                        gateway_unreachable = True
                        logger.warning("Gateway probed but returned 100%% packet loss (unreachable)")
                else:
                    stage2.status = StageStatus.FAILED
                    stage2.error_message = ping_res.error_message or "Gateway ping probe failed"
                    gateway_unreachable = True

                self.run_repo.update_stage(stage2)
                self.emit_event(
                    WorkflowEvent(
                        event_type=(
                            WorkflowEventType.STAGE_COMPLETED
                            if stage2.status == StageStatus.SUCCESS
                            else WorkflowEventType.STAGE_FAILED
                        ),
                        run_id=run.run_id,
                        stage_id=stage2.stage_id,
                        stage_name=stage2.stage_name,
                        stage_order=2,
                        status=stage2.status.value,
                        message=stage2.error_message,
                    )
                )

        except Exception as e:
            logger.error("Exception in Gateway Reachability stage: %s", e, exc_info=True)
            stage2.status = StageStatus.FAILED
            stage2.error_message = f"Gateway ping error: {e}"
            stage2.end_time_utc = now_utc_iso()
            gateway_unreachable = True
            self.run_repo.update_stage(stage2)
            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.STAGE_FAILED,
                    run_id=run.run_id,
                    stage_id=stage2.stage_id,
                    stage_name=stage2.stage_name,
                    stage_order=2,
                    status=StageStatus.FAILED.value,
                    message=stage2.error_message,
                )
            )

        if cancel_event is not None and cancel_event.is_set():
            return self._finalize_run(run, cancel_event)

        # ====================================================================
        # STAGE 3: Multi-Target External Probing
        # ====================================================================
        stage3 = TestStage(
            stage_id=new_id(),
            run_id=run.run_id,
            stage_name="External Targets Probe",
            stage_order=3,
            start_time_utc=now_utc_iso(),
            status=StageStatus.RUNNING,
        )
        self.run_repo.create_stage(stage3)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.STAGE_STARTED,
                run_id=run.run_id,
                stage_id=stage3.stage_id,
                stage_name=stage3.stage_name,
                stage_order=3,
                status=StageStatus.RUNNING.value,
            )
        )

        external_targets = [t for t in config.targets.external_targets.targets if t.enabled]
        any_external_succeeded = False

        try:
            for ext_cfg in external_targets:
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("Stage 3: Cancellation detected during external target iteration")
                    stage3.status = StageStatus.CANCELLED
                    stage3.error_message = "External probing cancelled by user"
                    break

                target_entity = Target(
                    target_id=ext_cfg.id,
                    target_type=TargetType.EXTERNAL_IP,
                    ip_address=ext_cfg.host,
                    provider_label=ext_cfg.label,
                    is_active=True,
                )
                self.obs_repo.create_target(target_entity)

                logger.info("Probing external target: %s (%s)", ext_cfg.id, mask_public_ip_address(ext_cfg.host))
                ext_ping_res: PingMeasurementResult = self.ping_adapter.execute(
                    target_ip=ext_cfg.host,
                    count=config.targets.external_targets.probe_count,
                    per_probe_timeout_ms=config.targets.external_targets.probe_timeout_ms,
                    target_type=TargetType.EXTERNAL_IP,
                )

                obs_list = ext_ping_res.to_observations(
                    run_id=run.run_id,
                    stage_id=stage3.stage_id,
                    target_id=ext_cfg.id,
                )
                self.obs_repo.create_observations_batch(obs_list)

                sum_list = ext_ping_res.to_summaries(
                    run_id=run.run_id,
                    target_id=ext_cfg.id,
                )
                self.summary_repo.create_summaries_batch(sum_list)

                if ext_ping_res.status == Status.SUCCESS and (ext_ping_res.packet_loss_pct or 0.0) < 100.0:
                    any_external_succeeded = True

            stage3.end_time_utc = now_utc_iso()
            if stage3.status != StageStatus.CANCELLED:
                stage3.status = StageStatus.SUCCESS if any_external_succeeded else StageStatus.FAILED
                if not any_external_succeeded:
                    stage3.error_message = "All external targets failed to respond"

            self.run_repo.update_stage(stage3)
            self.emit_event(
                WorkflowEvent(
                    event_type=(
                        WorkflowEventType.STAGE_CANCELLED
                        if stage3.status == StageStatus.CANCELLED
                        else (
                            WorkflowEventType.STAGE_COMPLETED
                            if stage3.status == StageStatus.SUCCESS
                            else WorkflowEventType.STAGE_FAILED
                        )
                    ),
                    run_id=run.run_id,
                    stage_id=stage3.stage_id,
                    stage_name=stage3.stage_name,
                    stage_order=3,
                    status=stage3.status.value,
                    message=stage3.error_message,
                )
            )

        except Exception as e:
            logger.error("Exception in External Targets Probe stage: %s", e, exc_info=True)
            stage3.status = StageStatus.FAILED
            stage3.error_message = f"External probing error: {e}"
            stage3.end_time_utc = now_utc_iso()
            self.run_repo.update_stage(stage3)
            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.STAGE_FAILED,
                    run_id=run.run_id,
                    stage_id=stage3.stage_id,
                    stage_name=stage3.stage_name,
                    stage_order=3,
                    status=StageStatus.FAILED.value,
                    message=stage3.error_message,
                )
            )

        if cancel_event is not None and cancel_event.is_set():
            return self._finalize_run(run, cancel_event)

        # ====================================================================
        # STAGE 4: DNS Resolution Timing and Status
        # ====================================================================
        stage4 = TestStage(
            stage_id=new_id(),
            run_id=run.run_id,
            stage_name="DNS Resolution",
            stage_order=4,
            start_time_utc=now_utc_iso(),
            status=StageStatus.RUNNING,
        )
        self.run_repo.create_stage(stage4)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.STAGE_STARTED,
                run_id=run.run_id,
                stage_id=stage4.stage_id,
                stage_name=stage4.stage_name,
                stage_order=4,
                status=StageStatus.RUNNING.value,
            )
        )

        resolvers = [r for r in config.targets.dns_resolvers.resolvers if r.enabled]
        domains = config.targets.dns_resolvers.test_domains
        any_dns_succeeded = False

        try:
            for r_cfg in resolvers:
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("Stage 4: Cancellation detected during DNS resolver iteration")
                    stage4.status = StageStatus.CANCELLED
                    stage4.error_message = "DNS resolution cancelled by user"
                    break

                resolver_ip = run.gateway_ip if r_cfg.host.lower() == "gateway" else r_cfg.host
                if not resolver_ip:
                    logger.warning("Skipping resolver %s; gateway IP unavailable", r_cfg.id)
                    continue

                r_target = Target(
                    target_id=r_cfg.id,
                    target_type=TargetType.DNS_RESOLVER,
                    ip_address=resolver_ip,
                    port=r_cfg.port,
                    provider_label=r_cfg.label,
                    is_active=True,
                )
                self.obs_repo.create_target(r_target)

                sample_idx = 1
                for domain in domains:
                    if cancel_event is not None and cancel_event.is_set():
                        stage4.status = StageStatus.CANCELLED
                        stage4.error_message = "DNS resolution cancelled by user"
                        break

                    logger.debug("DNS query: %s -> %s", domain, mask_public_ip_address(resolver_ip))
                    dns_res: DNSMeasurementResult = self.dns_adapter.execute(
                        resolver_ip=resolver_ip,
                        domain=domain,
                        port=r_cfg.port,
                        timeout_s=config.targets.dns_resolvers.timeout_ms / 1000.0,
                        resolver_id=r_cfg.id,
                    )

                    obs = dns_res.to_observation(
                        run_id=run.run_id,
                        stage_id=stage4.stage_id,
                        target_id=r_cfg.id,
                        sample_index=sample_idx,
                    )
                    self.obs_repo.create_observation(obs)
                    sample_idx += 1

                    if dns_res.status == Status.SUCCESS:
                        any_dns_succeeded = True

            stage4.end_time_utc = now_utc_iso()
            if stage4.status != StageStatus.CANCELLED:
                stage4.status = StageStatus.SUCCESS if any_dns_succeeded else StageStatus.FAILED
                if not any_dns_succeeded:
                    stage4.error_message = "All DNS queries failed"

            self.run_repo.update_stage(stage4)
            self.emit_event(
                WorkflowEvent(
                    event_type=(
                        WorkflowEventType.STAGE_CANCELLED
                        if stage4.status == StageStatus.CANCELLED
                        else (
                            WorkflowEventType.STAGE_COMPLETED
                            if stage4.status == StageStatus.SUCCESS
                            else WorkflowEventType.STAGE_FAILED
                        )
                    ),
                    run_id=run.run_id,
                    stage_id=stage4.stage_id,
                    stage_name=stage4.stage_name,
                    stage_order=4,
                    status=stage4.status.value,
                    message=stage4.error_message,
                )
            )

        except Exception as e:
            logger.error("Exception in DNS Resolution stage: %s", e, exc_info=True)
            stage4.status = StageStatus.FAILED
            stage4.error_message = f"DNS resolution error: {e}"
            stage4.end_time_utc = now_utc_iso()
            self.run_repo.update_stage(stage4)
            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.STAGE_FAILED,
                    run_id=run.run_id,
                    stage_id=stage4.stage_id,
                    stage_name=stage4.stage_name,
                    stage_order=4,
                    status=StageStatus.FAILED.value,
                    message=stage4.error_message,
                )
            )

        if cancel_event is not None and cancel_event.is_set():
            return self._finalize_run(run, cancel_event)

        # ====================================================================
        # STAGE 5: Throughput & Loaded Latency
        # ====================================================================
        stage5 = TestStage(
            stage_id=new_id(),
            run_id=run.run_id,
            stage_name="Throughput & Loaded Latency",
            stage_order=5,
            start_time_utc=now_utc_iso(),
            status=StageStatus.RUNNING,
        )
        self.run_repo.create_stage(stage5)
        self.emit_event(
            WorkflowEvent(
                event_type=WorkflowEventType.STAGE_STARTED,
                run_id=run.run_id,
                stage_id=stage5.stage_id,
                stage_name=stage5.stage_name,
                stage_order=5,
                status=StageStatus.RUNNING.value,
            )
        )

        try:
            if cancel_event is not None and cancel_event.is_set():
                stage5.status = StageStatus.CANCELLED
                stage5.error_message = "Throughput cancelled before execution"
                stage5.end_time_utc = now_utc_iso()
                self.run_repo.update_stage(stage5)
                self.emit_event(
                    WorkflowEvent(
                        event_type=WorkflowEventType.STAGE_CANCELLED,
                        run_id=run.run_id,
                        stage_id=stage5.stage_id,
                        stage_name=stage5.stage_name,
                        stage_order=5,
                        status=StageStatus.CANCELLED.value,
                    )
                )
                return self._finalize_run(run, cancel_event)

            # Skip policy per SRS-F-008: If upstream critical dependency failed
            if gateway_unreachable:
                stage5.status = StageStatus.SKIPPED
                stage5.skip_reason = "Gateway unreachable; downstream bandwidth-intensive stage skipped per SRS-F-008"
                stage5.end_time_utc = now_utc_iso()
                self.run_repo.update_stage(stage5)
                logger.info("Stage 5 skipped: %s", stage5.skip_reason)
                self.emit_event(
                    WorkflowEvent(
                        event_type=WorkflowEventType.STAGE_SKIPPED,
                        run_id=run.run_id,
                        stage_id=stage5.stage_id,
                        stage_name=stage5.stage_name,
                        stage_order=5,
                        status=StageStatus.SKIPPED.value,
                        message=stage5.skip_reason,
                    )
                )
            else:
                target_url = (
                    config.targets.throughput.lab_endpoint_url
                    if run.run_mode == RunMode.LAB
                    else config.targets.throughput.live_endpoint_url
                ) or ""

                tp_target = Target(
                    target_id="tp-endpoint",
                    target_type=TargetType.THROUGHPUT_ENDPOINT,
                    hostname=target_url,
                    provider_label="Throughput Measurement Endpoint",
                    is_active=True,
                )
                self.obs_repo.create_target(tp_target)

                if config.targets.loaded_latency.enabled:
                    probe_target_ip = run.gateway_ip or "1.1.1.1"
                    logger.info("Executing loaded latency comparison against %s with traffic to %s", probe_target_ip, target_url)
                    load_res: LoadedLatencyMeasurementResult = self.load_adapter.execute(
                        target_ip=probe_target_ip,
                        traffic_url=target_url,
                        cancel_event=cancel_event,
                    )
                    stage5.end_time_utc = now_utc_iso()

                    obs_list = load_res.to_observations(
                        run_id=run.run_id,
                        stage_id=stage5.stage_id,
                        target_id=tp_target.target_id,
                    )
                    self.obs_repo.create_observations_batch(obs_list)

                    if load_res.status == Status.SUCCESS:
                        stage5.status = StageStatus.SUCCESS
                    elif load_res.status == Status.CANCELLED:
                        stage5.status = StageStatus.CANCELLED
                        stage5.error_message = load_res.error_message
                    else:
                        stage5.status = StageStatus.FAILED
                        stage5.error_message = load_res.error_message
                else:
                    logger.info("Executing download throughput measurement against %s", target_url)
                    tp_res: ThroughputMeasurementResult = self.throughput_adapter.execute(
                        endpoint_url=target_url,
                        cancel_event=cancel_event,
                    )
                    stage5.end_time_utc = now_utc_iso()

                    obs_list = tp_res.to_observations(
                        run_id=run.run_id,
                        stage_id=stage5.stage_id,
                        target_id=tp_target.target_id,
                    )
                    self.obs_repo.create_observations_batch(obs_list)

                    if tp_res.status == Status.SUCCESS:
                        stage5.status = StageStatus.SUCCESS
                    elif tp_res.status == Status.CANCELLED:
                        stage5.status = StageStatus.CANCELLED
                        stage5.error_message = tp_res.error_message
                    else:
                        stage5.status = StageStatus.FAILED
                        stage5.error_message = tp_res.error_message

                self.run_repo.update_stage(stage5)
                self.emit_event(
                    WorkflowEvent(
                        event_type=(
                            WorkflowEventType.STAGE_CANCELLED
                            if stage5.status == StageStatus.CANCELLED
                            else (
                                WorkflowEventType.STAGE_COMPLETED
                                if stage5.status == StageStatus.SUCCESS
                                else WorkflowEventType.STAGE_FAILED
                            )
                        ),
                        run_id=run.run_id,
                        stage_id=stage5.stage_id,
                        stage_name=stage5.stage_name,
                        stage_order=5,
                        status=stage5.status.value,
                        message=stage5.error_message,
                    )
                )

        except Exception as e:
            logger.error("Exception in Throughput stage: %s", e, exc_info=True)
            stage5.status = StageStatus.FAILED
            stage5.error_message = f"Throughput error: {e}"
            stage5.end_time_utc = now_utc_iso()
            self.run_repo.update_stage(stage5)
            self.emit_event(
                WorkflowEvent(
                    event_type=WorkflowEventType.STAGE_FAILED,
                    run_id=run.run_id,
                    stage_id=stage5.stage_id,
                    stage_name=stage5.stage_name,
                    stage_order=5,
                    status=StageStatus.FAILED.value,
                    message=stage5.error_message,
                )
            )

        return self._finalize_run(run, cancel_event)

    def _finalize_run(
        self,
        run: TestRun,
        cancel_event: Optional[threading.Event] = None,
    ) -> TestRun:
        """Evaluate stage outcomes, compute final run status, and update persistence."""
        run.end_time_utc = now_utc_iso()
        persistence_error: Optional[str] = None

        try:
            stages = self.run_repo.get_stages_for_run(run.run_id)
        except Exception as db_err:
            logger.error("Failed to query stages during run finalization: %s", db_err)
            stages = []
            persistence_error = f"Database read failure during finalization: {db_err}"

        if persistence_error:
            run.status = RunStatus.FAILED
            run.overall_error = persistence_error
        else:
            is_cancelled = (cancel_event is not None and cancel_event.is_set()) or any(
                s.status == StageStatus.CANCELLED for s in stages
            )

            if is_cancelled:
                run.status = RunStatus.CANCELLED
                if not run.overall_error:
                    run.overall_error = "Diagnostic run was cancelled by user"
            elif stages and all(s.status in (StageStatus.SUCCESS, StageStatus.SKIPPED) for s in stages):
                run.status = RunStatus.COMPLETED
            elif any(s.status == StageStatus.SUCCESS for s in stages):
                run.status = RunStatus.PARTIAL
            else:
                run.status = RunStatus.FAILED

        try:
            self.run_repo.update_run(run)
        except Exception as db_err:
            logger.error("Failed to commit final run status to database: %s", db_err)
            run.status = RunStatus.FAILED
            run.overall_error = f"Database commit failure during finalization: {db_err}"

        event_type = (
            WorkflowEventType.RUN_COMPLETED
            if run.status == RunStatus.COMPLETED
            else (
                WorkflowEventType.RUN_CANCELLED
                if run.status == RunStatus.CANCELLED
                else (
                    WorkflowEventType.RUN_PARTIAL
                    if run.status == RunStatus.PARTIAL
                    else WorkflowEventType.RUN_FAILED
                )
            )
        )

        self.emit_event(
            WorkflowEvent(
                event_type=event_type,
                run_id=run.run_id,
                status=run.status.value,
                message=run.overall_error or f"Run completed with status {run.status.value}",
            )
        )
        logger.info("Diagnostic run %s finalized with status %s", run.run_id, run.status.value)
        return run
