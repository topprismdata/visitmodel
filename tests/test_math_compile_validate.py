# -*- coding: utf-8 -*-
"""L2 编译 + 独立验解全链路测试: Spec → Instance → (greedy 求解) → ValidationReport.

覆盖:
- MathCompiler 六条翻译规则 (R1-R6) 与守卫 (重复客户/未知保护/保护不足);
- MathValidator 全部违规类 (C0/C1/C2/C3/C4) 与 SourceMap 追溯;
- 端到端最小回路: 实现 SolverBackend 协议的贪心后端 → 验解 ok
  (后端定义在本测试文件内, 不进 algos/ — Phase A 不迁移旧算法).
"""
from datetime import date

import pytest
from visit_math_api import SolverConfig, SolveResult
from visit_math_api import VisitPlanningInstance  # noqa: F401  协议形参引用
from visit_semantic_api import (
    ContractType,
    CoreProtectionPolicy,
    PlanningHorizon,
    SemanticObjectivePolicy,
    SourceMetadata,
    VisitContract,
    VisitSemanticSpec,
    WorkloadCorridorPolicy,
)
from visitmodel import CONSTRAINT_IDS, MathCompiler, MathValidator

D = date


def _horizon() -> PlanningHorizon:
    return PlanningHorizon(
        start_date=D(2026, 3, 2),
        end_date=D(2026, 3, 13),
        timezone="Asia/Shanghai",
        calendar_id="cn",
        anchor_week=10,
        phase_period=2,
    )


def _contract(code, sigma, dates, phase=None) -> VisitContract:
    return VisitContract(
        customer_code=code,
        contract_type=ContractType.BIWEEKLY if phase is not None else ContractType.WEEKLY,
        phase=phase,
        sigma=sigma,
        legal_slot_indices=tuple(range(len(dates))),
        obligation=len(dates),
        legal_dates=dates,
    )


def _spec(corridor=(1, 3), protect=True) -> VisitSemanticSpec:
    return VisitSemanticSpec(
        horizon=_horizon(),
        contracts=(
            _contract("W1", 0, (D(2026, 3, 2), D(2026, 3, 9))),
            _contract("W2", 1, (D(2026, 3, 3), D(2026, 3, 10))),
            _contract("W3", 2, (D(2026, 3, 4), D(2026, 3, 11))),
            _contract("W4", 3, (D(2026, 3, 5), D(2026, 3, 12))),
            _contract("B1", 4, (D(2026, 3, 6),), phase=0),
            _contract("B2", 4, (D(2026, 3, 13),), phase=1),
        ),
        corridor=WorkloadCorridorPolicy(
            k_min=corridor[0], k_max=corridor[1],
            source="historical_baseline", derivation="manual", approved=True,
        ),
        core_protections=(
            (CoreProtectionPolicy(customer_code="W1", mandatory_visit=True),) if protect else ()
        ),
        objective_policy=SemanticObjectivePolicy(),
        metadata=SourceMetadata(
            schema_version="1.0", content_hash="h", source_snapshot_id="s",
            compiler_version="v", compiled_at="t",
        ),
    )


def _ideal_solution():
    """每工作日恰一店 (fixture 的构造最优)."""
    return {
        D(2026, 3, 2): ("W1",), D(2026, 3, 3): ("W2",), D(2026, 3, 4): ("W3",),
        D(2026, 3, 5): ("W4",), D(2026, 3, 6): ("B1",), D(2026, 3, 9): ("W1",),
        D(2026, 3, 10): ("W2",), D(2026, 3, 11): ("W3",), D(2026, 3, 12): ("W4",),
        D(2026, 3, 13): ("B2",),
    }


class TestMathCompiler:
    def test_compile_translates_all_fields(self):
        inst = MathCompiler().compile(_spec())
        assert inst.customers == ("W1", "W2", "W3", "W4", "B1", "B2")
        assert dict(inst.required_visits) == {"W1": 2, "W2": 2, "W3": 2, "W4": 2, "B1": 1, "B2": 1}
        assert inst.day_corridor == (1, 3)
        assert inst.fixed_visits == frozenset({"W1"})
        assert len(inst.workdays) == 10  # 两周 × 5 工作日, 升序去重
        assert inst.workdays[0] == D(2026, 3, 2) and inst.workdays[-1] == D(2026, 3, 13)
        assert inst.eligible_days["B1"] == frozenset({D(2026, 3, 6)})

    def test_objective_translation_deterministic(self):
        terms = MathCompiler().compile(_spec()).objective_terms
        assert [(t.metric, t.priority) for t in terms] == [
            ("total_distance", 1), ("daily_count_range", 2), ("change_count", 3),
        ]

    def test_objective_ignore_drops_term(self):
        spec = _spec()
        policy = SemanticObjectivePolicy(plan_stability="ignore")
        object.__setattr__(spec, "objective_policy", policy)  # frozen: 测试内替换策略
        terms = MathCompiler().compile(spec).objective_terms
        assert [t.metric for t in terms] == ["total_distance", "daily_count_range"]

    def test_source_map_complete(self):
        inst = MathCompiler().compile(_spec())
        assert dict(inst.source_map.entries) == dict(CONSTRAINT_IDS)
        assert inst.source_map.explain("C1_OBLIGATION") == "contract_obligation"
        assert inst.source_map.explain("NOPE") == "unmapped"

    def test_metadata_flattened(self):
        inst = MathCompiler().compile(_spec())
        assert inst.metadata["source_snapshot_id"] == "s"
        assert inst.metadata["schema_version"] == "1.0"

    def test_rejects_duplicate_customer(self):
        bad = _spec()
        dup = _contract("W1", 0, (D(2026, 3, 2),))
        object.__setattr__(bad, "contracts", bad.contracts + (dup,))
        with pytest.raises(ValueError, match="duplicate customer_code"):
            MathCompiler().compile(bad)

    def test_rejects_protection_for_unknown_customer(self):
        bad = _spec()
        object.__setattr__(
            bad, "core_protections",
            (CoreProtectionPolicy(customer_code="GHOST", mandatory_visit=True),),
        )
        with pytest.raises(ValueError, match="unknown customers"):
            MathCompiler().compile(bad)

    def test_rejects_undercovered_protected_customer(self):
        bad = _spec()
        c = bad.contract_of("W1")
        starved = VisitContract(
            customer_code=c.customer_code, contract_type=c.contract_type, phase=c.phase,
            sigma=c.sigma, legal_slot_indices=(0,), obligation=2, legal_dates=(D(2026, 3, 2),),
        )
        contracts = tuple(starved if x.customer_code == "W1" else x for x in bad.contracts)
        object.__setattr__(bad, "contracts", contracts)
        with pytest.raises(ValueError, match="legal_dates 1 < obligation 2"):
            MathCompiler().compile(bad)


class TestMathValidator:
    def test_ideal_solution_passes(self):
        report = MathValidator().validate(MathCompiler().compile(_spec()), _ideal_solution())
        assert report.ok, report.violations
        assert report.violations == ()

    def test_missing_visit_trips_obligation_and_corridor_min(self):
        sol = dict(_ideal_solution())
        del sol[D(2026, 3, 13)]  # B2 缺访 + 该日 0 < k_min
        report = MathValidator().validate(MathCompiler().compile(_spec()), sol)
        ids = {x.constraint_id for x in report.violations}
        assert not report.ok
        assert {"C1_OBLIGATION", "C2_CORRIDOR_MIN"} <= ids

    def test_same_day_dup_trips_c0_and_c1(self):
        sol = dict(_ideal_solution())
        sol[D(2026, 3, 2)] = ("W1", "W1")  # 同日重复 → 计 2 次, W1 总数 3 ≠ 2
        report = MathValidator().validate(MathCompiler().compile(_spec()), sol)
        ids = {x.constraint_id for x in report.violations}
        assert {"C1_OBLIGATION", "C0_SAME_DAY_DUP"} <= ids
        v = next(x for x in report.violations if x.constraint_id == "C1_OBLIGATION")
        assert v.semantic_rule_id == "contract_obligation"
        assert "W1" in v.detail and "3 visits" in v.detail

    def test_eligibility_violation_via_cross_weekday_insert(self):
        sol = dict(_ideal_solution())
        sol[D(2026, 3, 3)] = ("W2", "W1")  # W1 的合法日只有周一 — C3
        report = MathValidator().validate(MathCompiler().compile(_spec()), sol)
        c3 = [x for x in report.violations if x.constraint_id == "C3_ELIGIBILITY"]
        assert len(c3) == 1 and c3[0].semantic_rule_id == "contract_legal_slots"

    def test_unknown_customer_and_day(self):
        sol = dict(_ideal_solution())
        sol[D(2026, 3, 16)] = ("W1",)  # 视野外的工作日
        sol[D(2026, 3, 4)] = ("W3", "GHOST")
        report = MathValidator().validate(MathCompiler().compile(_spec()), sol)
        ids = {x.constraint_id for x in report.violations}
        assert {"C0_UNKNOWN_DAY", "C0_UNKNOWN_CUSTOMER"} <= ids

    def test_empty_solution_fails_everything(self):
        report = MathValidator().validate(MathCompiler().compile(_spec()), {})
        assert not report.ok
        ids = {x.constraint_id for x in report.violations}
        assert "C1_OBLIGATION" in ids and "C2_CORRIDOR_MIN" in ids

    def test_zero_corridor_means_unbounded(self):
        inst = MathCompiler().compile(_spec(corridor=(0, 0), protect=False))
        sol = dict(_ideal_solution())
        sol[D(2026, 3, 2)] = ("W1", "W2", "W3")  # 3 家一天, 无界走廊放行 (义务守恒仍破坏)
        report = MathValidator().validate(inst, sol)
        assert not any(x.constraint_id.startswith("C2_") for x in report.violations)


class _GreedyBackend:
    """最小 SolverBackend 实现 (测试专用): 按日期顺序装满仍欠义务的合法客户."""

    def solve(self, instance, config: SolverConfig) -> SolveResult:
        remaining = dict(instance.required_visits)
        assignments = {}
        for d in instance.workdays:
            day = []
            for c in instance.customers:
                if remaining.get(c, 0) > 0 and d in instance.eligible_days.get(c, frozenset()):
                    day.append(c)
                    remaining[c] -= 1
            assignments[d] = tuple(day)
        ok = all(v == 0 for v in remaining.values())
        return SolveResult(
            status="OPTIMAL" if ok else "INFEASIBLE",
            assignments=assignments,
            objective_vector=(0.0,) * len(instance.objective_terms),
            termination_reason="exhaustive-deterministic",
            instance_hash=instance.metadata.get("content_hash", ""),
        )


class TestEndToEndCycle:
    def test_compile_solve_validate_green_path(self):
        """全链路: L1 语义规格 → L2 数学实例 → L3 协议后端 → L2 独立验解."""
        from visit_math_api import SolverBackend  # runtime_checkable

        inst = MathCompiler().compile(_spec())
        backend = _GreedyBackend()
        assert isinstance(backend, SolverBackend)  # 协议结构匹配
        result = backend.solve(inst, SolverConfig(backend="greedy", seed=7))
        assert result.status == "OPTIMAL"
        report = MathValidator().validate(inst, result.assignments)
        assert report.ok, report.violations

    def test_validator_is_independent_of_backend_internals(self):
        """G3: 后端谎报 status=OPTIMAL 也骗不过验解器."""
        inst = MathCompiler().compile(_spec())
        result = _GreedyBackend().solve(inst, SolverConfig(backend="greedy"))
        broken = dict(result.assignments)
        del broken[D(2026, 3, 5)]
        lie = SolveResult(
            status="OPTIMAL", assignments=broken,
            objective_vector=result.objective_vector,
        )
        assert not MathValidator().validate(inst, lie.assignments).ok
