"""Dynamic diagnostic stage cards and measurement presentation for TicNet.

Provides structured, real-time presentation of empirical measurements across
the five diagnostic pipeline stages:
1. Default Gateway Discovery
2. Gateway Reachability & Delay
3. Multi-Target External Probing
4. Direct DNS Query Timing
5. Throughput & Loaded Latency
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import sqlite3
from typing import Any, Dict, List, Optional, Union

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ticnet.desktop.theme import ThemeTokens
from ticnet.models.entities import Observation, Summary, Target, TestRun, TestStage
from ticnet.models.enums import MeasurementStatus, StageStatus, TargetType
from ticnet.persistence.database import DatabaseManager


class StageCardState(str, Enum):
    """Supported display states for diagnostic stage cards."""

    IDLE = "IDLE"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    INCONCLUSIVE = "INCONCLUSIVE"


# =============================================================================
# Stage Measurement Normalized Data Models
# =============================================================================


@dataclass
class Stage1Data:
    """Normalized measurement data for Stage 1: Default Gateway Discovery."""

    gateway_ip: Optional[str] = None
    interface_name: Optional[str] = None
    interface_type: Optional[str] = None
    discovery_source: Optional[str] = None
    status: str = "IDLE"
    error_message: Optional[str] = None


@dataclass
class Stage2Data:
    """Normalized measurement data for Stage 2: Gateway Reachability & Delay."""

    packet_loss_pct: Optional[float] = None
    median_rtt_ms: Optional[float] = None
    min_rtt_ms: Optional[float] = None
    avg_rtt_ms: Optional[float] = None
    max_rtt_ms: Optional[float] = None
    p95_rtt_ms: Optional[float] = None
    jitter_macd_ms: Optional[float] = None
    probe_count_attempted: int = 0
    probe_count_successful: int = 0
    status: str = "IDLE"
    error_message: Optional[str] = None


@dataclass
class Stage3TargetResult:
    """Individual external target probing outcome."""

    target_id: str
    host: str
    label: str
    packet_loss_pct: Optional[float] = None
    median_rtt_ms: Optional[float] = None
    is_reachable: bool = False


@dataclass
class Stage3Data:
    """Normalized measurement data for Stage 3: Multi-Target External Probing."""

    targets: List[Stage3TargetResult] = field(default_factory=list)
    reachable_count: int = 0
    total_count: int = 0
    overall_median_rtt_ms: Optional[float] = None
    status: str = "IDLE"
    error_message: Optional[str] = None


@dataclass
class Stage4DNSResult:
    """Individual DNS query result."""

    domain: str
    resolver_ip: str
    response_time_ms: Optional[float] = None
    rcode_name: Optional[str] = None
    resolution_status: Optional[str] = None
    has_answer: bool = False


@dataclass
class Stage4Data:
    """Normalized measurement data for Stage 4: Direct DNS Query Timing."""

    queries: List[Stage4DNSResult] = field(default_factory=list)
    tested_domain: Optional[str] = None
    resolver_ip: Optional[str] = None
    response_time_ms: Optional[float] = None
    rcode_name: Optional[str] = None
    resolution_status: Optional[str] = None
    status: str = "IDLE"
    error_message: Optional[str] = None


@dataclass
class Stage5Data:
    """Normalized measurement data for Stage 5: Throughput & Loaded Latency."""

    throughput_mbps: Optional[float] = None
    baseline_median_rtt_ms: Optional[float] = None
    loaded_median_rtt_ms: Optional[float] = None
    delta_rtt_median_ms: Optional[float] = None
    bufferbloat_ratio: Optional[float] = None
    duration_cap_hit: bool = False
    byte_cap_hit: bool = False
    is_skipped: bool = False
    skip_reason: Optional[str] = None
    status: str = "IDLE"
    error_message: Optional[str] = None


# =============================================================================
# Measurement Extractor (Safe & Non-blocking)
# =============================================================================


class StageMeasurementExtractor:
    """Extracts and normalizes empirical measurements from persistence or raw telemetry."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db

    def extract_stage1(
        self,
        run_id: str,
        observations: Optional[List[Observation]] = None,
        run_entity: Optional[TestRun] = None,
    ) -> Stage1Data:
        """Extract verified gateway discovery telemetry."""
        gateway_ip = None
        interface_name = None
        interface_type = None
        discovery_source = None
        status = "SUCCESS"
        error_message = None

        if run_entity:
            gateway_ip = run_entity.gateway_ip
            interface_name = run_entity.interface_name
            interface_type = run_entity.interface_type

        # Look in observations for method metadata
        if observations:
            for obs in observations:
                if obs.metric_name == "gateway_discovery":
                    if obs.status != MeasurementStatus.SUCCESS:
                        status = "FAILED"
                        error_message = obs.error_message or "Gateway discovery failed"
                    if obs.method_metadata:
                        try:
                            meta = json.loads(obs.method_metadata)
                            gateway_ip = gateway_ip or meta.get("gateway_ip")
                            interface_name = interface_name or meta.get("interface")
                            discovery_source = meta.get("source")
                        except Exception:
                            pass
                    break

        # Fallback to direct DB query if needed and available
        if self.db:
            try:
                with self.db.connection() as conn:
                    if not gateway_ip:
                        cursor = conn.execute(
                            "SELECT gateway_ip, interface_name, interface_type FROM test_runs WHERE run_id = ?;",
                            (run_id,),
                        )
                        row = cursor.fetchone()
                        if row and row["gateway_ip"]:
                            gateway_ip = row["gateway_ip"]
                            interface_name = row["interface_name"]
                            interface_type = row["interface_type"]

                    if not gateway_ip or status == "IDLE":
                        cursor2 = conn.execute(
                            "SELECT method_metadata, status, error_message FROM observations WHERE run_id = ? AND metric_name = 'gateway_discovery';",
                            (run_id,),
                        )
                        row2 = cursor2.fetchone()
                        if row2:
                            if row2["method_metadata"]:
                                try:
                                    meta = json.loads(row2["method_metadata"])
                                    gateway_ip = meta.get("gateway_ip") or gateway_ip
                                    interface_name = meta.get("interface") or interface_name
                                    discovery_source = meta.get("source") or discovery_source
                                except Exception:
                                    pass
                            if row2["status"] != "SUCCESS":
                                status = "FAILED"
                                error_message = row2["error_message"] or "Gateway discovery failed"
                            elif gateway_ip:
                                status = "SUCCESS"
            except Exception:
                pass

        if not gateway_ip and status == "SUCCESS":
            status = "IDLE"

        return Stage1Data(
            gateway_ip=gateway_ip,
            interface_name=interface_name,
            interface_type=interface_type,
            discovery_source=discovery_source or "Unprivileged Routing Table",
            status=status,
            error_message=error_message,
        )

    def extract_stage2(
        self,
        run_id: str,
        summaries: Optional[List[Summary]] = None,
        observations: Optional[List[Observation]] = None,
    ) -> Stage2Data:
        """Extract gateway reachability, statistical RTT, and delay variation."""
        packet_loss = None
        median_rtt = None
        min_rtt = None
        avg_rtt = None
        max_rtt = None
        p95_rtt = None
        jitter_macd = None
        attempted = 0
        successful = 0
        status = "SUCCESS"
        error_message = None

        # 1. Parse from summaries
        if summaries:
            for s in summaries:
                if s.target_id == "gateway" or s.metric_name in ("packet_loss", "round_trip_time", "delay_variation"):
                    if s.metric_name == "packet_loss":
                        packet_loss = s.value_numeric
                        attempted = s.sample_count_total
                    elif s.metric_name == "round_trip_time":
                        if s.statistic_name == "median":
                            median_rtt = s.value_numeric
                            successful = s.sample_count_valid
                        elif s.statistic_name == "p95":
                            p95_rtt = s.value_numeric
                    elif s.metric_name == "delay_variation":
                        jitter_macd = s.value_numeric

        # 2. Compute min/avg/max from observations
        if observations:
            rtts = [
                obs.value_numeric
                for obs in observations
                if obs.metric_name == "round_trip_time" and obs.value_numeric is not None
            ]
            if rtts:
                min_rtt = min(rtts)
                max_rtt = max(rtts)
                avg_rtt = sum(rtts) / len(rtts)
                if successful == 0:
                    successful = len(rtts)
                if attempted == 0:
                    attempted = len(observations)

        # 3. Fallback to DB if empty
        if packet_loss is None and self.db:
            try:
                with self.db.connection() as conn:
                    cursor = conn.execute(
                        "SELECT metric_name, statistic_name, value_numeric, sample_count_total, sample_count_valid "
                        "FROM summaries WHERE run_id = ?;",
                        (run_id,),
                    )
                    for row in cursor.fetchall():
                        m_name = row["metric_name"]
                        s_name = row["statistic_name"]
                        val = row["value_numeric"]
                        if m_name == "packet_loss":
                            packet_loss = val
                            attempted = row["sample_count_total"]
                        elif m_name == "round_trip_time" and s_name == "median":
                            median_rtt = val
                            successful = row["sample_count_valid"]
                        elif m_name == "round_trip_time" and s_name == "p95":
                            p95_rtt = val
                        elif m_name == "delay_variation":
                            jitter_macd = val
            except Exception:
                pass

        if min_rtt is None and self.db:
            try:
                with self.db.connection() as conn:
                    cursor = conn.execute(
                        "SELECT value_numeric FROM observations WHERE run_id = ? AND metric_name = 'round_trip_time' AND target_id = 'gateway' AND status = 'SUCCESS';",
                        (run_id,),
                    )
                    rtts = [row["value_numeric"] for row in cursor.fetchall() if row["value_numeric"] is not None]
                    if rtts:
                        min_rtt = min(rtts)
                        max_rtt = max(rtts)
                        avg_rtt = sum(rtts) / len(rtts)
            except Exception:
                pass

        return Stage2Data(
            packet_loss_pct=packet_loss,
            median_rtt_ms=median_rtt,
            min_rtt_ms=min_rtt,
            avg_rtt_ms=avg_rtt,
            max_rtt_ms=max_rtt,
            p95_rtt_ms=p95_rtt,
            jitter_macd_ms=jitter_macd,
            probe_count_attempted=attempted,
            probe_count_successful=successful,
            status=status,
            error_message=error_message,
        )

    def extract_stage3(
        self,
        run_id: str,
        summaries: Optional[List[Summary]] = None,
        targets: Optional[List[Target]] = None,
    ) -> Stage3Data:
        """Extract multi-target external probing results."""
        target_results: List[Stage3TargetResult] = []
        reachable_count = 0
        total_count = 0
        medians: List[float] = []

        # Map target summaries by target_id
        sum_by_target: Dict[str, Dict[str, float]] = {}

        if summaries:
            for s in summaries:
                if s.target_id and s.target_id != "gateway":
                    if s.target_id not in sum_by_target:
                        sum_by_target[s.target_id] = {}
                    if s.value_numeric is not None:
                        sum_by_target[s.target_id][s.metric_name] = s.value_numeric

        # DB fallback for targets and summaries if not provided
        if (summaries is None or targets is None) and self.db:
            try:
                with self.db.connection() as conn:
                    if summaries is None:
                        cursor = conn.execute(
                            "SELECT target_id, metric_name, value_numeric FROM summaries WHERE run_id = ? AND target_id != 'gateway';",
                            (run_id,),
                        )
                        for row in cursor.fetchall():
                            t_id = row["target_id"]
                            m_name = row["metric_name"]
                            val = row["value_numeric"]
                            if t_id not in sum_by_target:
                                sum_by_target[t_id] = {}
                            if val is not None:
                                sum_by_target[t_id][m_name] = val

                    if targets is None:
                        cursor = conn.execute(
                            "SELECT target_id, target_type, ip_address, hostname, provider_label "
                            "FROM targets WHERE target_type = 'EXTERNAL_IP' ORDER BY target_id ASC;"
                        )
                        for row in cursor.fetchall():
                            total_count += 1
                            t_id = row["target_id"]
                            host = row["ip_address"] or row["hostname"] or t_id
                            lbl = row["provider_label"] or t_id
                            t_metrics = sum_by_target.get(t_id, {})
                            loss = t_metrics.get("packet_loss")
                            rtt = t_metrics.get("round_trip_time")
                            is_reach = loss is not None and loss < 100.0
                            if is_reach:
                                reachable_count += 1
                                if rtt is not None:
                                    medians.append(rtt)

                            target_results.append(
                                Stage3TargetResult(
                                    target_id=t_id,
                                    host=host,
                                    label=lbl,
                                    packet_loss_pct=loss,
                                    median_rtt_ms=rtt,
                                    is_reachable=is_reach,
                                )
                            )
            except Exception:
                pass

        # Map targets if passed in memory
        if targets:
            for t in targets:
                if t.target_type == TargetType.EXTERNAL_IP:
                    total_count += 1
                    t_metrics = sum_by_target.get(t.target_id, {})
                    loss = t_metrics.get("packet_loss")
                    rtt = t_metrics.get("round_trip_time")
                    is_reach = loss is not None and loss < 100.0
                    if is_reach:
                        reachable_count += 1
                        if rtt is not None:
                            medians.append(rtt)

                    target_results.append(
                        Stage3TargetResult(
                            target_id=t.target_id,
                            host=t.ip_address or t.hostname or t.target_id,
                            label=t.provider_label or t.target_id,
                            packet_loss_pct=loss,
                            median_rtt_ms=rtt,
                            is_reachable=is_reach,
                        )
                    )

        overall_median = (sum(medians) / len(medians)) if medians else None

        return Stage3Data(
            targets=target_results,
            reachable_count=reachable_count,
            total_count=total_count,
            overall_median_rtt_ms=overall_median,
            status="SUCCESS" if reachable_count > 0 else ("FAILED" if total_count > 0 else "IDLE"),
        )

    def extract_stage4(
        self,
        run_id: str,
        observations: Optional[List[Observation]] = None,
    ) -> Stage4Data:
        """Extract direct DNS query timing and resolution status."""
        queries: List[Stage4DNSResult] = []
        tested_domain = None
        resolver_ip = None
        response_time = None
        rcode_name = None
        res_status = None
        error_msg = None

        if observations is None and self.db:
            try:
                with self.db.connection() as conn:
                    cursor = conn.execute(
                        "SELECT metric_name, value_numeric, status, error_message, method_metadata "
                        "FROM observations WHERE run_id = ? AND metric_name = 'dns_query_time';",
                        (run_id,),
                    )
                    for row in cursor.fetchall():
                        domain = "unknown"
                        res_ip = "127.0.0.1"
                        rcode = "NOERROR"
                        r_status = "RESOLVED"
                        has_ans = False

                        if row["method_metadata"]:
                            try:
                                meta = json.loads(row["method_metadata"])
                                domain = meta.get("domain", domain)
                                res_ip = meta.get("resolver_ip", res_ip)
                                rcode = meta.get("rcode_name", rcode)
                                r_status = meta.get("resolution_status", r_status)
                                has_ans = meta.get("has_answer", False)
                            except Exception:
                                pass

                        rtt = row["value_numeric"] if row["status"] == "SUCCESS" else None
                        q_res = Stage4DNSResult(
                            domain=domain,
                            resolver_ip=res_ip,
                            response_time_ms=rtt,
                            rcode_name=rcode,
                            resolution_status=r_status,
                            has_answer=has_ans,
                        )
                        queries.append(q_res)

                        if not tested_domain:
                            tested_domain = domain
                            resolver_ip = res_ip
                            response_time = rtt
                            rcode_name = rcode
                            res_status = r_status
                            if row["status"] != "SUCCESS":
                                error_msg = row["error_message"]
            except Exception:
                pass
        elif observations:
            for obs in observations:
                if obs.metric_name == "dns_query_time":
                    domain = "unknown"
                    res_ip = "127.0.0.1"
                    rcode = "NOERROR"
                    r_status = "RESOLVED"
                    has_ans = False

                    if obs.method_metadata:
                        try:
                            meta = json.loads(obs.method_metadata)
                            domain = meta.get("domain", domain)
                            res_ip = meta.get("resolver_ip", res_ip)
                            rcode = meta.get("rcode_name", rcode)
                            r_status = meta.get("resolution_status", r_status)
                            has_ans = meta.get("has_answer", False)
                        except Exception:
                            pass

                    rtt = obs.value_numeric if obs.status == MeasurementStatus.SUCCESS else None
                    q_res = Stage4DNSResult(
                        domain=domain,
                        resolver_ip=res_ip,
                        response_time_ms=rtt,
                        rcode_name=rcode,
                        resolution_status=r_status,
                        has_answer=has_ans,
                    )
                    queries.append(q_res)

                    if not tested_domain:
                        tested_domain = domain
                        resolver_ip = res_ip
                        response_time = rtt
                        rcode_name = rcode
                        res_status = r_status
                        if obs.status != MeasurementStatus.SUCCESS:
                            error_msg = obs.error_message

        return Stage4Data(
            queries=queries,
            tested_domain=tested_domain,
            resolver_ip=resolver_ip,
            response_time_ms=response_time,
            rcode_name=rcode_name,
            resolution_status=res_status,
            status="SUCCESS" if res_status == "RESOLVED" else ("FAILED" if res_status else "IDLE"),
            error_message=error_msg,
        )

    def extract_stage5(
        self,
        run_id: str,
        observations: Optional[List[Observation]] = None,
        stage_entity: Optional[TestStage] = None,
    ) -> Stage5Data:
        """Extract throughput and loaded latency bufferbloat telemetry."""
        tp_mbps = None
        baseline_rtt = None
        loaded_rtt = None
        delta_rtt = None
        ratio = None
        is_skipped = False
        skip_reason = None
        error_msg = None

        if self.db:
            try:
                with self.db.connection() as conn:
                    if stage_entity is None:
                        cursor = conn.execute(
                            "SELECT status, skip_reason, error_message FROM test_stages WHERE run_id = ? AND (stage_name = 'throughput_loaded_latency' OR stage_order = 5);",
                            (run_id,),
                        )
                        row = cursor.fetchone()
                        if row:
                            if row["status"] == "SKIPPED":
                                is_skipped = True
                                skip_reason = row["skip_reason"]
                            elif row["status"] == "FAILED":
                                error_msg = row["error_message"]

                    if observations is None:
                        cursor = conn.execute(
                            "SELECT metric_name, value_numeric, status, error_message, method_metadata "
                            "FROM observations WHERE run_id = ? AND (metric_name = 'download_throughput' OR metric_name = 'loaded_latency_delta');",
                            (run_id,),
                        )
                        for row in cursor.fetchall():
                            m_name = row["metric_name"]
                            if m_name == "loaded_latency_delta" and row["method_metadata"]:
                                try:
                                    meta = json.loads(row["method_metadata"])
                                    tp_mbps = tp_mbps or meta.get("load_throughput_mbps")
                                    baseline_rtt = meta.get("baseline_median_rtt_ms")
                                    loaded_rtt = meta.get("loaded_median_rtt_ms")
                                    delta_rtt = meta.get("delta_rtt_median_ms")
                                    ratio = meta.get("bufferbloat_ratio")
                                except Exception:
                                    pass
                            elif m_name == "download_throughput":
                                tp_mbps = row["value_numeric"]
            except Exception:
                pass

        if stage_entity:
            if stage_entity.status == StageStatus.SKIPPED:
                is_skipped = True
                skip_reason = stage_entity.skip_reason or "Stage skipped"
            elif stage_entity.status == StageStatus.FAILED:
                error_msg = stage_entity.error_message

        if observations:
            for obs in observations:
                if obs.metric_name == "loaded_latency_delta":
                    if obs.method_metadata:
                        try:
                            meta = json.loads(obs.method_metadata)
                            tp_mbps = meta.get("load_throughput_mbps")
                            baseline_rtt = meta.get("baseline_median_rtt_ms")
                            loaded_rtt = meta.get("loaded_median_rtt_ms")
                            delta_rtt = meta.get("delta_rtt_median_ms")
                            ratio = meta.get("bufferbloat_ratio")
                        except Exception:
                            pass
                elif obs.metric_name == "download_throughput":
                    tp_mbps = obs.value_numeric

        return Stage5Data(
            throughput_mbps=tp_mbps,
            baseline_median_rtt_ms=baseline_rtt,
            loaded_median_rtt_ms=loaded_rtt,
            delta_rtt_median_ms=delta_rtt,
            bufferbloat_ratio=ratio,
            is_skipped=is_skipped,
            skip_reason=skip_reason,
            status="SKIPPED" if is_skipped else ("SUCCESS" if tp_mbps is not None else "IDLE"),
            error_message=error_msg,
        )


# =============================================================================
# Diagnostic Stage Card UI Widget
# =============================================================================


class DiagnosticStageCard(QFrame):
    """Enhanced dynamic status card for an individual diagnostic pipeline stage.

    Preserves 100% backward compatibility:
    - Inherits from QFrame (satisfies stage_boxes[order] contract).
    - Exposes lbl_status (satisfies stage_status_labels[order] contract).
    - Renders honest telemetry with explicit zero/none distinction.
    """

    def __init__(
        self,
        order: int,
        stage_num: str,
        name: str,
        desc: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.order: int = order
        self.stage_num: str = stage_num
        self.stage_name: str = name
        self.desc_text: str = desc

        self.current_state: StageCardState = StageCardState.IDLE
        self.setObjectName(f"stageBox_{order}")

        self._init_ui()

    def _init_ui(self) -> None:
        """Construct card hierarchy with title, status badge, status text, and telemetry area."""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.setMinimumHeight(135)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # 1. Header: Stage Title + Semantic Status Badge
        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        self.lbl_title = QLabel(f"<b>{self.stage_num}:</b> {self.stage_name}", top_row)
        self.lbl_title.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ThemeTokens.TEXT_PRIMARY}; border: none;"
        )
        top_layout.addWidget(self.lbl_title)
        top_layout.addStretch()

        self.lbl_badge = QLabel("IDLE", top_row)
        self.lbl_badge.setStyleSheet(self._get_badge_style(StageCardState.IDLE))
        top_layout.addWidget(self.lbl_badge)
        layout.addWidget(top_row)

        # 2. Status Label (Strict Compatibility with stage_status_labels[order])
        self.lbl_status = QLabel("Status: Idle (Ready)", self)
        self.lbl_status.setObjectName(f"lbl_stage_status_{self.order}")
        self.lbl_status.setStyleSheet(
            f"color: {ThemeTokens.TEXT_MUTED}; font-family: {ThemeTokens.FONT_MONO}; font-size: 11px; font-weight: 600; border: none;"
        )
        layout.addWidget(self.lbl_status)

        # 3. Dynamic Telemetry & Measurement Panel
        self.telemetry_frame = QFrame(self)
        self.telemetry_frame.setObjectName("stageTelemetryFrame")
        self.telemetry_layout = QVBoxLayout(self.telemetry_frame)
        self.telemetry_layout.setContentsMargins(10, 8, 10, 8)
        self.telemetry_layout.setSpacing(4)
        layout.addWidget(self.telemetry_frame)

        # Initial Idle Placeholder
        self._set_idle_telemetry()
        self._apply_card_style(StageCardState.IDLE)

    # -------------------------------------------------------------------------
    # Public State and Measurement Modifiers
    # -------------------------------------------------------------------------

    def set_stage_state(
        self,
        state: Union[StageCardState, str],
        status_text: Optional[str] = None,
    ) -> None:
        """Update card execution state and status headline."""
        if isinstance(state, StageCardState):
            norm_state = state
        else:
            clean = str(state).strip().upper()
            try:
                norm_state = StageCardState(clean)
            except ValueError:
                if "RUN" in clean or "PROGRESS" in clean:
                    norm_state = StageCardState.RUNNING
                elif "COMPLET" in clean or "PASS" in clean or "SUCCESS" in clean:
                    norm_state = StageCardState.SUCCESS
                elif "SKIP" in clean:
                    norm_state = StageCardState.SKIPPED
                elif "FAIL" in clean or "ERR" in clean:
                    norm_state = StageCardState.FAILED
                elif "INCONCLU" in clean:
                    norm_state = StageCardState.INCONCLUSIVE
                elif "PEND" in clean:
                    norm_state = StageCardState.PENDING
                else:
                    norm_state = StageCardState.IDLE

        self.current_state = norm_state
        self.lbl_badge.setText(norm_state.value)
        self.lbl_badge.setStyleSheet(self._get_badge_style(norm_state))
        self._apply_card_style(norm_state)

        if status_text:
            self.lbl_status.setText(status_text)
            self._update_status_label_style(norm_state)

    def set_measurements(
        self,
        data: Union[Stage1Data, Stage2Data, Stage3Data, Stage4Data, Stage5Data, dict],
    ) -> None:
        """Populate the telemetry panel with verified empirical measurements."""
        self._clear_telemetry_rows()

        if isinstance(data, Stage1Data):
            self._render_stage1_metrics(data)
        elif isinstance(data, Stage2Data):
            self._render_stage2_metrics(data)
        elif isinstance(data, Stage3Data):
            self._render_stage3_metrics(data)
        elif isinstance(data, Stage4Data):
            self._render_stage4_metrics(data)
        elif isinstance(data, Stage5Data):
            self._render_stage5_metrics(data)
        elif isinstance(data, dict):
            self._render_dict_metrics(data)

    def update_progress(self, message: str) -> None:
        """Update active progress message during stage execution."""
        self.lbl_status.setText(f"Status: {self.stage_name} — {message}")

    def reset(self) -> None:
        """Reset stage card to pending/idle state and clear all previous measurements."""
        self.set_stage_state(StageCardState.PENDING, "Status: Pending")
        self._set_idle_telemetry()
        self._apply_card_style(StageCardState.PENDING)

    # -------------------------------------------------------------------------
    # Internal Telemetry Rendering
    # -------------------------------------------------------------------------

    def _clear_telemetry_rows(self) -> None:
        """Remove all existing measurement rows from the telemetry layout."""
        while self.telemetry_layout.count():
            item = self.telemetry_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()

    def _set_idle_telemetry(self) -> None:
        """Render idle state placeholder with stage description."""
        self._clear_telemetry_rows()
        lbl = QLabel(self.desc_text, self.telemetry_frame)
        lbl.setStyleSheet(f"color: {ThemeTokens.TEXT_MUTED}; font-size: 11px; line-height: 1.3;")
        lbl.setWordWrap(True)
        self.telemetry_layout.addWidget(lbl)

    def _add_metric_row(
        self,
        key: str,
        value: str,
        is_highlight: bool = False,
        val_color: Optional[str] = None,
    ) -> None:
        """Add a key-value metric row with clean monospaced value."""
        row = QWidget(self.telemetry_frame)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        lbl_k = QLabel(f"{key}:", row)
        lbl_k.setStyleSheet(
            f"font-size: 11px; color: {ThemeTokens.TEXT_MUTED}; font-weight: 600; border: none; background: transparent;"
        )
        row_layout.addWidget(lbl_k)

        color = val_color or (ThemeTokens.BRAND_PRIMARY if is_highlight else ThemeTokens.TEXT_PRIMARY)
        weight = "700" if is_highlight else "500"

        lbl_v = QLabel(value, row)
        lbl_v.setStyleSheet(
            f"font-family: {ThemeTokens.FONT_MONO}; font-size: 11px; color: {color}; font-weight: {weight}; border: none; background: transparent;"
        )
        lbl_v.setWordWrap(True)
        row_layout.addWidget(lbl_v)
        row_layout.addStretch()

        self.telemetry_layout.addWidget(row)

    def _render_stage1_metrics(self, data: Stage1Data) -> None:
        if data.gateway_ip:
            self._add_metric_row("Gateway IP", data.gateway_ip, is_highlight=True)
            if data.interface_name:
                intf_str = f"{data.interface_name} ({data.interface_type or 'IPv4'})"
                self._add_metric_row("Interface", intf_str)
            if data.discovery_source:
                self._add_metric_row("Discovery Source", data.discovery_source)
        else:
            msg = data.error_message or "Default gateway not discovered"
            self._add_metric_row("Discovery Result", msg, val_color=ThemeTokens.COLOR_ERROR)

    def _render_stage2_metrics(self, data: Stage2Data) -> None:
        # 1. Packet Loss (Strict Invariant: 0.0% is not missing)
        if data.packet_loss_pct is not None:
            loss_str = f"{data.packet_loss_pct:.1f}% ({data.probe_count_attempted - data.probe_count_successful}/{data.probe_count_attempted} dropped)"
            loss_color = ThemeTokens.COLOR_SUCCESS if data.packet_loss_pct == 0.0 else ThemeTokens.COLOR_ERROR
            self._add_metric_row("Packet Loss", loss_str, is_highlight=True, val_color=loss_color)
        else:
            self._add_metric_row("Packet Loss", "N/A (Unmeasured)", val_color=ThemeTokens.TEXT_MUTED)

        # 2. Median RTT
        if data.median_rtt_ms is not None:
            rtt_parts = [f"Median: {data.median_rtt_ms:.2f} ms"]
            if data.min_rtt_ms is not None and data.max_rtt_ms is not None:
                rtt_parts.append(f"Min: {data.min_rtt_ms:.2f} | Max: {data.max_rtt_ms:.2f}")
            self._add_metric_row("Round-Trip Time", " | ".join(rtt_parts), is_highlight=True)
        else:
            self._add_metric_row("Round-Trip Time", "N/A (No successful probes)", val_color=ThemeTokens.TEXT_MUTED)

        # 3. P95 Latency (Only if sample count >= 10)
        if data.p95_rtt_ms is not None:
            self._add_metric_row("P95 Latency", f"{data.p95_rtt_ms:.2f} ms (n={data.probe_count_successful})")

        # 4. Jitter / MACD (Only if sample count >= 2)
        if data.jitter_macd_ms is not None:
            self._add_metric_row("MACD Jitter", f"{data.jitter_macd_ms:.2f} ms delay variation")

    def _render_stage3_metrics(self, data: Stage3Data) -> None:
        summary_str = f"{data.reachable_count}/{data.total_count} Targets Reachable"
        if data.overall_median_rtt_ms is not None:
            summary_str += f" | Median RTT: {data.overall_median_rtt_ms:.1f} ms"
        reach_color = ThemeTokens.COLOR_SUCCESS if data.reachable_count == data.total_count and data.total_count > 0 else (ThemeTokens.COLOR_WARNING if data.reachable_count > 0 else ThemeTokens.COLOR_ERROR)
        self._add_metric_row("External Reachability", summary_str, is_highlight=True, val_color=reach_color)

        # Target breakdown rows
        for t in data.targets[:3]:  # Top 3 targets for compact display
            status_text = f"{t.median_rtt_ms:.1f} ms" if t.median_rtt_ms is not None else "Timeout"
            loss_text = f" (Loss: {t.packet_loss_pct:.0f}%)" if t.packet_loss_pct is not None else ""
            self._add_metric_row(t.label or t.host, f"{status_text}{loss_text}")

    def _render_stage4_metrics(self, data: Stage4Data) -> None:
        if data.tested_domain:
            self._add_metric_row("Target Domain", f"{data.tested_domain} (UDP port 53)")
        if data.resolver_ip:
            self._add_metric_row("DNS Resolver", data.resolver_ip)
        if data.response_time_ms is not None:
            self._add_metric_row("Response Time", f"{data.response_time_ms:.1f} ms", is_highlight=True)
        if data.rcode_name:
            self._add_metric_row("RCODE Status", f"{data.rcode_name} ({data.resolution_status or 'RESOLVED'})")
        elif data.error_message:
            self._add_metric_row("Resolution Error", data.error_message, val_color=ThemeTokens.COLOR_ERROR)

    def _render_stage5_metrics(self, data: Stage5Data) -> None:
        if data.is_skipped:
            self._add_metric_row("Execution Policy", "Stage Skipped per SRS-F-008", is_highlight=True, val_color=ThemeTokens.COLOR_WARNING)
            self._add_metric_row("Skip Reason", data.skip_reason or "Upstream gateway unreachable")
            self._add_metric_row("Throughput", "N/A (Bypassed)", val_color=ThemeTokens.TEXT_MUTED)
        else:
            if data.throughput_mbps is not None:
                self._add_metric_row("Downstream Speed", f"{data.throughput_mbps:.1f} Mbps", is_highlight=True)
            else:
                self._add_metric_row("Downstream Speed", "N/A (Unmeasured)", val_color=ThemeTokens.TEXT_MUTED)

            if data.loaded_median_rtt_ms is not None and data.baseline_median_rtt_ms is not None:
                delta_str = f"{data.loaded_median_rtt_ms:.1f} ms vs baseline {data.baseline_median_rtt_ms:.1f} ms"
                if data.delta_rtt_median_ms is not None:
                    delta_str += f" (+{data.delta_rtt_median_ms:.1f} ms)"
                self._add_metric_row("Loaded Latency", delta_str)

            if data.bufferbloat_ratio is not None:
                self._add_metric_row("Bufferbloat Ratio", f"{data.bufferbloat_ratio:.2f}x baseline")

    def _render_dict_metrics(self, data: dict) -> None:
        for k, v in data.items():
            self._add_metric_row(str(k), str(v))

    # -------------------------------------------------------------------------
    # Styling Helpers
    # -------------------------------------------------------------------------

    def _get_badge_style(self, state: StageCardState) -> str:
        styles = {
            StageCardState.RUNNING: (
                f"background-color: {ThemeTokens.BRAND_LIGHT}; color: {ThemeTokens.BRAND_PRIMARY}; "
                f"border: 1px solid #bfdbfe; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.SUCCESS: (
                f"background-color: {ThemeTokens.BG_SUCCESS_LIGHT}; color: #065f46; "
                f"border: 1px solid {ThemeTokens.BORDER_SUCCESS}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.FAILED: (
                f"background-color: {ThemeTokens.BG_ERROR_LIGHT}; color: #991b1b; "
                f"border: 1px solid {ThemeTokens.BORDER_ERROR}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.SKIPPED: (
                f"background-color: {ThemeTokens.BG_WARNING_LIGHT}; color: #92400e; "
                f"border: 1px solid {ThemeTokens.BORDER_WARNING}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.INCONCLUSIVE: (
                f"background-color: {ThemeTokens.BG_INCONCLUSIVE_LIGHT}; color: #3730a3; "
                f"border: 1px solid {ThemeTokens.BORDER_INCONCLUSIVE}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.PENDING: (
                f"background-color: {ThemeTokens.BG_MUTED}; color: {ThemeTokens.TEXT_MUTED}; "
                f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
            StageCardState.IDLE: (
                f"background-color: {ThemeTokens.BG_MUTED}; color: {ThemeTokens.TEXT_MUTED}; "
                f"border: 1px solid {ThemeTokens.BORDER_DEFAULT}; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 7px;"
            ),
        }
        return styles.get(state, styles[StageCardState.IDLE])

    def _apply_card_style(self, state: StageCardState) -> None:
        border_col = ThemeTokens.BORDER_DEFAULT
        bg_col = "#ffffff"
        left_border = "1px solid " + ThemeTokens.BORDER_DEFAULT

        if state == StageCardState.RUNNING:
            border_col = "#bfdbfe"
            left_border = f"4px solid {ThemeTokens.BRAND_PRIMARY}"
        elif state == StageCardState.SUCCESS:
            border_col = ThemeTokens.BORDER_SUCCESS
            left_border = f"4px solid {ThemeTokens.COLOR_SUCCESS}"
        elif state == StageCardState.FAILED:
            border_col = ThemeTokens.BORDER_ERROR
            left_border = f"4px solid {ThemeTokens.COLOR_ERROR}"
        elif state == StageCardState.SKIPPED:
            border_col = ThemeTokens.BORDER_WARNING
            left_border = f"4px solid {ThemeTokens.COLOR_WARNING}"
        elif state == StageCardState.INCONCLUSIVE:
            border_col = ThemeTokens.BORDER_INCONCLUSIVE
            left_border = f"4px solid {ThemeTokens.COLOR_INCONCLUSIVE}"

        self.setStyleSheet(
            f"#stageBox_{self.order} {{"
            f"  background-color: {bg_col};"
            f"  border: 1px solid {border_col};"
            f"  border-left: {left_border};"
            f"  border-radius: {ThemeTokens.RADIUS_DEFAULT};"
            f"}}"
            f"#stageTelemetryFrame {{"
            f"  background-color: {ThemeTokens.BG_MUTED};"
            f"  border: 1px solid {ThemeTokens.BORDER_DEFAULT};"
            f"  border-radius: 6px;"
            f"}}"
        )

    def _update_status_label_style(self, state: StageCardState) -> None:
        colors = {
            StageCardState.RUNNING: ThemeTokens.BRAND_PRIMARY,
            StageCardState.SUCCESS: ThemeTokens.COLOR_SUCCESS,
            StageCardState.FAILED: ThemeTokens.COLOR_ERROR,
            StageCardState.SKIPPED: ThemeTokens.COLOR_WARNING,
            StageCardState.INCONCLUSIVE: ThemeTokens.COLOR_INCONCLUSIVE,
            StageCardState.PENDING: ThemeTokens.TEXT_MUTED,
            StageCardState.IDLE: ThemeTokens.TEXT_MUTED,
        }
        col = colors.get(state, ThemeTokens.TEXT_MUTED)
        self.lbl_status.setStyleSheet(
            f"color: {col}; font-family: {ThemeTokens.FONT_MONO}; font-size: 11px; font-weight: 600; border: none;"
        )
