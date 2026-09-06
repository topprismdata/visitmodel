# VisitModel — 周期拜访问题的数学建模层（Problem Formulation Layer）

> **Status**: 立项 2026-09-06 · 骨架阶段（内容随母项目 Task 2/3 落地迁入）
> **定位**: 三层架构的第二层（MLIR 语义下的"问题方言"）：把 VisitIR 的语义事实**降级（lower）**为求解器无关的数学结构。对应五段纪律中的 `lower`。
> **依赖**: `visit-ir`（唯一依赖）。**禁含**: 任何求解器驱动循环、元启发式骨架、领域搜索算子（那些归 OptiCore）。

## 职责（What belongs here）

| 组件 | 内容 | 种子来源（母项目） |
|---|---|---|
| **Obligation 降级** | VisitIR `ObligationSet` → 覆盖约束右端；`(contract, calendar, cycle-hypothesis) → 义务集` 的唯一 grounding 实现 | 新建（v0.1 D1） |
| **SP 方言** | 集合划分主问题：每日恰选一列、义务覆盖、z 星期几线性化、合同池过滤、对偶价语义 | `algos/sp_matheuristic.py` 模型构造段（`_fw_table`/`_contract_pool_filter`/`sp_solve_lp`/`sp_solve_ip`） |
| **TSP 方言** | 单日开环哈密顿链的 CP-SAT `AddCircuit` 公式 + 求解凭证（status/objective/bound/gap） | `algos/tsp_engine.py` |
| **定价问题定义** | 列生成的定价子问题**定义**（rc 公式、合法性域），不含循环 | `price_columns` 的公式部分 |
| **JSON Schema** | 模型实例的版本化交换（`visit_model/sp@v1` 等） | 新建 |

## 铁律

1. 符号/地面分离（AML 传统）：模型定义 ≠ 实例数据；
2. 求解器可替换：同一 SP 方言可落 CP-SAT / GLOP / SCIP；
3. `parse ≠ validate ≠ lower ≠ solve ≠ certify`——本仓库只做 `lower`；
4. 每个公式组件必须有"可行集 == 语义合法集"级别的等价测试（暴力枚举对拍）。

## 关联项目

- 上游：[`VisitIR`](/Users/ghb/VisitIR)（语义层）
- 下游：[`OptiCore`](/Users/ghb/OptiCore)（通用求解引擎，零领域依赖）
- 母项目：`visit-scheduling-optimizer`（应用组装 + 实验流水线）
