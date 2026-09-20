# -*- coding: utf-8 -*-
"""MathCompiler — L2 确定性翻译器: VisitSemanticSpec → VisitPlanningInstance.

> 出处: docs/design/THREE_LAYER_ARCHITECTURE_v0.2.md §4.

每条翻译规则都有 constraint_id, 并经 SourceMap 回指 semantic_rule_id
(G2: No orphan mathematical constraint). 本模块零业务判断:
- 不重新推导合同/相位/槽位语义 (那是 L1 check_contract 的零例外闸);
- 不从 days_orig 推断 K_min/K_max (v0.1 泄漏修复);
- 不感知求解器.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from visit_math_api import (
    EMPTY_MAP,
    ObjectiveTerm,
    SourceMap,
    VisitPlanningInstance,
)
from visit_semantic_api import SemanticObjectivePolicy, SourceMetadata, VisitSemanticSpec

# 数学约束 ID → 语义规则 ID (唯一出处, SourceMap 与 Validator 共用)
CONSTRAINT_IDS: Mapping[str, str] = MappingProxyType(
    {
        "C0_UNKNOWN_DAY": "horizon_workdays",
        "C0_UNKNOWN_CUSTOMER": "contract_registry",
        "C0_SAME_DAY_DUP": "single_visit_per_day",
        "C1_OBLIGATION": "contract_obligation",
        "C2_CORRIDOR_MIN": "workload_corridor_policy",
        "C2_CORRIDOR_MAX": "workload_corridor_policy",
        "C3_ELIGIBILITY": "contract_legal_slots",
        "C4_FIXED_VISIT": "core_protection_mandatory",
    }
)

# SemanticObjectivePolicy 字段 → ObjectiveTerm (lexicographic 优先级固定)
_OBJECTIVE_TRANSLATION: tuple[tuple[str, str, int], ...] = (
    # (policy字段, metric, priority)  — 字段值 "ignore" 则跳过
    ("distance", "total_distance", 1),
    ("workload_balance", "daily_count_range", 2),
    ("plan_stability", "change_count", 3),
)


class MathCompiler:
    """L2 编译器: 语义规格 → 数学实例 (确定性, 可重演)."""

    def compile(self, spec: VisitSemanticSpec) -> VisitPlanningInstance:
        """翻译规则 (每条 = SemanticSpec 字段 → Instance 字段):

        R1 contracts[].customer_code/obligation → customers/required_visits   (C1)
        R2 contracts[].legal_dates            → eligible_days                 (C3)
        R3 corridor.k_min/k_max               → day_corridor                  (C2)
        R4 core_protections[mandatory_visit]  → fixed_visits                  (C4)
        R5 ∪ contracts[].legal_dates          → workdays (升序去重)
        R6 objective_policy                   → objective_terms (确定性表)
        """
        customers = tuple(c.customer_code for c in spec.contracts)
        if len(set(customers)) != len(customers):
            raise ValueError("duplicate customer_code in spec.contracts")
        required = {c.customer_code: int(c.obligation) for c in spec.contracts}
        eligible = {c.customer_code: frozenset(c.legal_dates) for c in spec.contracts}
        fixed = frozenset(
            p.customer_code for p in spec.core_protections if p.mandatory_visit
        )
        unknown_fixed = fixed - set(customers)
        if unknown_fixed:
            raise ValueError(f"core_protection references unknown customers: {sorted(unknown_fixed)}")
        workdays = tuple(sorted({d for dates in eligible.values() for d in dates}))
        # C4 完整性: mandatory 客户的 eligible_dates 必须覆盖其全部义务日期
        for p in spec.core_protections:
            if p.mandatory_visit:
                contract = spec.contract_of(p.customer_code)
                if len(contract.legal_dates) < contract.obligation:
                    raise ValueError(
                        f"protected customer {p.customer_code}: "
                        f"legal_dates {len(contract.legal_dates)} < obligation {contract.obligation}"
                    )
        return VisitPlanningInstance(
            customers=customers,
            workdays=workdays,
            required_visits=MappingProxyType(required),
            day_corridor=(spec.corridor.k_min, spec.corridor.k_max),
            eligible_days=MappingProxyType(eligible),
            fixed_visits=fixed,
            objective_terms=self._translate_objective(spec.objective_policy),
            source_map=SourceMap(entries=CONSTRAINT_IDS),
            metadata=self._flat_metadata(spec.metadata),
        )

    @staticmethod
    def _translate_objective(policy: SemanticObjectivePolicy) -> tuple[ObjectiveTerm, ...]:
        """确定性表驱动翻译 (R6); 字段值 "ignore" → 该目标缺席."""
        terms = []
        for attr, metric, priority in _OBJECTIVE_TRANSLATION:
            if getattr(policy, attr) != "ignore":
                terms.append(ObjectiveTerm(metric=metric, priority=priority, sense="minimize"))
        return tuple(terms)

    @staticmethod
    def _flat_metadata(meta: SourceMetadata | None) -> Mapping[str, str]:
        """SourceMetadata → 扁平 str→str (避免 L3 传递依赖语义类型)."""
        if meta is None:
            return EMPTY_MAP
        return MappingProxyType(
            {
                "schema_version": meta.schema_version,
                "content_hash": meta.content_hash,
                "source_snapshot_id": meta.source_snapshot_id,
                "compiler_version": meta.compiler_version,
                "compiled_at": meta.compiled_at,
            }
        )
