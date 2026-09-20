# -*- coding: utf-8 -*-
"""MathValidator — L2 独立验解器 (G3: Solver 算答案, Model Validator 验答案).

> 出处: docs/design/THREE_LAYER_ARCHITECTURE_v0.2.md §4.4.

与 core/metric.check_capacity / check_freq 的关系:
- 那些是 L3 内联快筛 (solver 自用), 保留;
- 本类是跨层权威验解: 只依赖 VisitPlanningInstance, 独立于任何 SolverBackend,
  并为每条违规给出 SourceMap 语义追溯 (constraint_id → semantic_rule_id).

语义口径 (与既有实现逐条对齐):
- C1 义务: 每客户总拜访次数 == required_visits (check_freq 同义);
- C2 走廊: 对 instance.workdays 全集施行 [k_min, k_max], 0=无界
  (check_capacity 同义; 且对 solution 缺席的工作日按 0 访问检查 —
  比 L3 内联更严, 防止 solver 删日逃避 k_min);
- C3 合法日期: 每次拜访的日期 ∈ eligible_days[客户] (合同+相位的编译结果);
- C4 核心保全: mandatory 客户义务必须精确履行;
- C0 结构: 未知日期 / 未知客户 / 同日重复拜访.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Mapping, Sequence

from visit_math_api import SourceMap, ValidationReport, VisitPlanningInstance, Violation
from visitmodel.compiler import CONSTRAINT_IDS

_WORKDAY_SET = "C0_UNKNOWN_DAY"
_CUSTOMER_SET = "C0_UNKNOWN_CUSTOMER"
_SAME_DAY_DUP = "C0_SAME_DAY_DUP"
_OBLIGATION = "C1_OBLIGATION"
_CORRIDOR_MIN = "C2_CORRIDOR_MIN"
_CORRIDOR_MAX = "C2_CORRIDOR_MAX"
_ELIGIBILITY = "C3_ELIGIBILITY"
_FIXED = "C4_FIXED_VISIT"


class MathValidator:
    """独立验解: instance + assignments → ValidationReport (纯函数语义)."""

    def __init__(self, source_map: SourceMap | None = None):
        self._source_map: SourceMap = source_map if source_map is not None else SourceMap(entries=CONSTRAINT_IDS)

    def validate(
        self,
        instance: VisitPlanningInstance,
        solution: Mapping[date, Sequence[str]],
    ) -> ValidationReport:
        """校验 assignments 是否满足 instance 的全部数学约束.

        注意: 本方法只看数学实例, 不接触 VisitSemanticSpec / 合同 / 相位 —
        层级纪律: 语义正确性由 L1 编译闸保证, 数学可行性由本类保证.
        """
        v: list[Violation] = []
        workdays = set(instance.workdays)
        customers = set(instance.customers)
        required = instance.required_visits
        eligible = instance.eligible_days

        # ---- 结构检查 + 单店计数 (一次遍历) ----
        visits: dict[str, int] = defaultdict(int)
        for d, seq in solution.items():
            if d not in workdays:
                v.append(self._v(_WORKDAY_SET, f"date {d} not in instance.workdays"))
            seen: set[str] = set()
            for c in seq:
                if c not in customers:
                    v.append(self._v(_CUSTOMER_SET, f"unknown customer {c} on {d}"))
                    continue
                if c in seen:
                    v.append(self._v(_SAME_DAY_DUP, f"customer {c} visited twice on {d}"))
                seen.add(c)
                visits[c] += 1
                if d not in eligible.get(c, frozenset()):
                    v.append(self._v(_ELIGIBILITY, f"customer {c} on ineligible date {d}"))

        # ---- C1 义务守恒 (+C4 核心保全) ----
        for c in instance.customers:
            got = visits.get(c, 0)
            want = required.get(c, 0)
            if got != want:
                v.append(self._v(_OBLIGATION, f"customer {c}: {got} visits != obligation {want}"))
            elif c in instance.fixed_visits and got < want:
                # 冗余保险 (got==want 已隐含), 防未来义务松弛语义引入后漏检
                v.append(self._v(_FIXED, f"protected customer {c}: {got} < {want}"))

        # ---- C2 走廊 (对全部工作日, 缺席日按 0) ----
        k_min, k_max = instance.day_corridor
        for d in instance.workdays:
            n = len(solution.get(d, ()))
            if k_max and n > k_max:
                v.append(self._v(_CORRIDOR_MAX, f"{d}: {n} > k_max {k_max}"))
            if k_min and n < k_min:
                v.append(self._v(_CORRIDOR_MIN, f"{d}: {n} < k_min {k_min}"))

        return ValidationReport(ok=not v, violations=tuple(v))

    def _v(self, constraint_id: str, detail: str) -> Violation:
        return Violation(
            constraint_id=constraint_id,
            semantic_rule_id=self._source_map.explain(constraint_id),
            detail=detail,
        )
