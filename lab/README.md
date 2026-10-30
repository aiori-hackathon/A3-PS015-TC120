# TicNet In-Memory Simulated Laboratory Validation

**Status:** Implemented & Verified (Phase 11)  
**Related Documents:** `docs/phase11/PHASE11_LAB_ARCHITECTURE.md`, `docs/phase11/PHASE11_SCENARIO_VALIDATION_REPORT.md`, `09_LAB_SAFETY_RUNBOOK.md`

---

## 1. Safety Mandate & Simulation Architecture

The TicNet Laboratory is designed strictly for controlled evaluation of diagnostic inference rules against known synthetic network conditions using **In-Memory Simulated Laboratory Validation**:

1. **Scenario A (`TC-LAB-01`):** Default Gateway Unreachable (`R-GW-01`).
2. **Scenario B (`TC-LAB-02`):** DNS Resolution Failure (`R-DNS-01`).
3. **Scenario C (`TC-LAB-03`):** Induced Multi-Target External Packet Loss (`R-LOSS-01`).
4. **Scenario C Guardrail (`TC-LAB-03b`):** Single-Target Loss Anti-Hallucination Guardrail (`R-INCONCLUSIVE-01`).
5. **Scenario D (`TC-LAB-04`):** Network Congestion & Bufferbloat (`R-LOAD-01`).

### Scope and Operating Disclosure

- Validation is conducted via high-fidelity in-memory simulated measurement adapters executing against the real `WorkflowOrchestrator`, `DiagnosisEngine`, and SQLite persistence layers on Windows (`uac_admin=False`).
- **Physical or virtual-machine / hypervisor network fault injection has not yet been validated.**
- 100% specification adherence under controlled in-memory simulation does not constitute a claim of 100% real-world diagnostic accuracy across arbitrary consumer broadband environments.

### Crucial Safety Gate Invariant

**No fault simulation may ever be dispatched to a production, corporate, or live home network.**

All executions require affirmative operator confirmation (`--i-confirm-isolated-lab`) via `lab/safety_gate.py`. The safety gate acts as an application-layer logical guardrail, failing closed if physical interfaces, public IP subnets, or forbidden destinations are targeted.

---

## 2. Quarantined Ground Truth & Evaluator

- Diagnostic runs operate completely blind: ground truth parameters are recorded independently in the SQLite `lab_trials` table, segregated from `DiagnosisEngine` query scopes.
- Post-run evaluation by `LabEvaluator` (`TC-LAB-06`) deterministically scores outcomes without circular dependencies. Single-target guardrail trials (`TC-LAB-03b`) are classified as Expected Inconclusive Guardrail Successes.
