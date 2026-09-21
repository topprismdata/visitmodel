# -*- coding: utf-8 -*-
"""visit_math_api — 数学层跨层类型契约(纯类型, 零实现, 零依赖).

> 出处: docs/design/THREE_LAYER_ARCHITECTURE_v0.2.md §3-§5 (GPT 评审 P0-2/P0-3 修正).

依赖规则 (CI 架构测试强制, 见 tests/test_architecture_layers.py):
- L3 (algos/**) 只允许 import 本包, 禁止 import visit_ir / visit_semantic_api
  / core.contract / visitmodel;
- L2 (visitmodel/**) 实现本包协议, 可 import visit_semantic_api, 禁止 import
  algos / visit_ir / ortools;
- 本包自己零依赖 (不 import 任何实现包 / numpy / ortools).

铁律:
- 一切边界类型 frozen=True; 禁止裸 dict/list 字段 (Mapping 输入在构造侧
  包 MappingProxyType, 此处仅声明 Mapping 只读视图);
- metadata 为扁平 str→str 溯源 (避免 L3 经传递依赖接触语义类型);
- 多目标结果一律 objective_vector, 禁止坍缩成单 float (v0.1 教训).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, Sequence, runtime_checkable

__all__ = [
    "SourceMap",
    "ObjectiveTerm",
    "VisitPlanningInstance",
    "SolverConfig",
    "SolveResult",
    "Violation",
    "ValidationReport",
    "SolverBackend",
    "DecisionEpisode",
    "episode_hash",
]

EMPTY_MAP: Mapping = MappingProxyType({})


def _empty_map() -> Mapping:
    """dataclass 默认值工厂 (mappingproxy 实例不可直接作 default)."""
    return MappingProxyType({})


@dataclass(frozen=True)
class SourceMap:
    """constraint_id → semantic_rule_id 追溯映射 (G2, 编译器 debug-symbol).

    链路: 业务规则 → semantic_rule_id → constraint_id → 求解器证据
    → SourceMap.explain() → 业务解释 / Decision Episode.
    """

    entries: Mapping[str, str]

    def explain(self, constraint_id: str) -> str:
        """数学约束 ID → 语义规则 ID; 未映射返回 'unmapped' (fail-loud 由调用方决定)."""
        return self.entries.get(constraint_id, "unmapped")


@dataclass(frozen=True)
class ObjectiveTerm:
    """单目标项 (L2 从 SemanticObjectivePolicy 确定性翻译)."""

    metric: str  # "total_distance" | "max_daily_distance" | "daily_count_range" | "change_count"
    priority: int  # lexicographic 优先级, 1 = 最高
    sense: str = "minimize"


@dataclass(frozen=True)
class VisitPlanningInstance:
    """L2 编译产物: 求解器无关的领域数学实例(不可变).

    v0.1 命名 VisitMathIR → v0.2 更名 (GPT 路线 2): 本类型是保留
    visit-scheduling 数学结构的领域实例, 非通用优化 IR.

    v0.1 P0-2 修复: 候选列池 / 暖启动 / incumbent / 对偶价 是 L3 求解
    运行时状态, 一律不属于本类型 (见 algos 内 SolveRuntimeState).
    """

    customers: tuple[str, ...]
    workdays: tuple[date, ...]
    required_visits: Mapping[str, int]  # customer_code → obligation (只读)
    day_corridor: tuple[int, int]  # (k_min, k_max); 0 = 无界
    eligible_days: Mapping[str, frozenset]  # customer_code → frozenset[date]
    fixed_visits: frozenset  # mandatory_visit 客户 (C4)
    objective_terms: tuple[ObjectiveTerm, ...]
    source_map: SourceMap
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))  # 扁平溯源
    slot_dates: Mapping[str, Mapping] = field(default_factory=lambda: MappingProxyType({}))
    # slot_dates: customer_code → {weekday: 该合同在该星期几的槽位日期集}
    # (R2′ 换挡语义; 缺省空 = C3 退化为 eligible_days 严格口径)


@dataclass(frozen=True)
class SolverConfig:
    """L3 求解配置 (L2 不感知具体取值)."""

    backend: str  # "greedy" | "alns" | "cp_sat" | ...
    time_limit_s: float = 60.0
    seed: int = 0
    params: Mapping[str, str] = field(default_factory=_empty_map)


@dataclass(frozen=True)
class SolveResult:
    """L3 → Orchestrator 的求解结果 (v0.1 大幅扩充, GPT B5).

    status 语义:
    - OPTIMAL / FEASIBLE: assignments 可行, violated_constraints 必为空;
    - INFEASIBLE: L3 不自创业务事件 (P0-1), 只报数学事实 —
      violated_constraints 给出 constraint_id, 由 L2 SourceMap 追溯解释.
    """

    status: str  # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE"
    assignments: Mapping[date, tuple[str, ...]]  # date → 有序客户序列 (路线顺序)
    objective_vector: tuple[float, ...]  # 与 instance.objective_terms 对齐
    best_bound: Optional[float] = None
    gap: Optional[float] = None
    violated_constraints: tuple[str, ...] = ()  # INFEASIBLE 时非空
    termination_reason: str = ""
    solver_stats: Mapping[str, float] = field(default_factory=_empty_map)  # iters/accepted/elapsed_s
    instance_hash: str = ""  # G4: 所解实例指纹


@dataclass(frozen=True)
class Violation:
    """单条违规: 数学事实 + 语义追溯 + 人话细节."""

    constraint_id: str  # e.g. "C1_OBLIGATION"
    semantic_rule_id: str  # 经 SourceMap 追溯, e.g. "contract_obligation"
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    """L2 MathValidator 独立验解结论 (G3: Solver 不自证合法)."""

    ok: bool
    violations: tuple[Violation, ...] = ()


@runtime_checkable
class SolverBackend(Protocol):
    """L3 求解后端协议 (L2 定义, L3 实现).

    实现者只看到 VisitPlanningInstance + SolverConfig — 不知道什么是
    核心客户 / 走廊为什么是 17~29 / 频次为什么是 4.
    """

    def solve(self, instance: VisitPlanningInstance, config: SolverConfig) -> SolveResult: ...


@dataclass(frozen=True)
class DecisionEpisode:
    """决策留痕最小 envelope (v0.2 §7, G4 可复现性的运行时载体).

    目标: 今天生成的计划, 将来还能回答 — "这个结果是在什么业务事实、
    什么规则版本、什么数学模型、什么 solver 参数下产生的?"
    完整 Decision Memory 后置; 本 envelope 现在就必须随每次求解落账.
    """

    episode_id: str
    source_snapshot_id: str
    semantic_spec_version: str
    semantic_spec_hash: str
    instance_hash: str
    solver_backend: str
    solver_version: str
    solver_config_hash: str
    seed: int
    solution_id: str
    status: str
    termination_reason: str
    exception_grant_ids: tuple = ()
    decision_timestamp: str = ""


def episode_hash(episode: DecisionEpisode) -> str:
    """确定性指纹 (不含 episode_id / timestamp — 同一决策重放同哈希)."""
    import hashlib
    import json
    payload = {
        "source_snapshot_id": episode.source_snapshot_id,
        "semantic_spec_version": episode.semantic_spec_version,
        "semantic_spec_hash": episode.semantic_spec_hash,
        "instance_hash": episode.instance_hash,
        "solver_backend": episode.solver_backend,
        "solver_version": episode.solver_version,
        "solver_config_hash": episode.solver_config_hash,
        "seed": episode.seed,
        "solution_id": episode.solution_id,
        "status": episode.status,
        "termination_reason": episode.termination_reason,
        "exception_grant_ids": list(episode.exception_grant_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def sequence_days(solution: Mapping[date, Sequence[str]]) -> Mapping[date, tuple[str, ...]]:
    """把可变序列解规范化为 frozen 元组视图 (Orchestrator/L2 入口防御)."""
    return MappingProxyType({d: tuple(seq) for d, seq in solution.items()})
