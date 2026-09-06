# VisitModel 设计草案 · 2026-09-06

## 1. 边界

**进来**：义务降级（Obligation→约束 RHS）、SP 公式（LP/IP + z 线性化 + 池过滤 + 对偶语义）、TSP 开链公式、定价问题定义、模型 JSON Schema。
**不进来**：ALNS/HGS/退火/轮盘赌（→ OptiCore）、数据解析（→ 应用层）、LLM 适配（→ VisitIR 边界）、SRP xlsx（→ 应用层前端）。

## 2. 模块规划

```
src/visit_model/
├── obligations.py     # VisitIR ObligationSet → 覆盖约束规格 (grounding 唯一实现)
├── sp/
│   ├── formulation.py # 集合划分 LP/IP 构建器 (solver-agnostic 结构 + ortools 绑定)
│   ├── z_linear.py    # 星期几一致 + 合同覆盖 RHS 线性化
│   └── pricing.py     # 定价子问题定义 (rc 公式 + 合法域), 循环归 OptiCore
├── tsp/
│   └── open_chain.py  # 开环哈密顿链公式 + 求解凭证契约
└── schemas/           # visit_model/sp@v1 JSON Schema
```

## 3. 与母项目的迁移映射

| 母项目现址 | 去向 | 备注 |
|---|---|---|
| `sp_matheuristic._fw_table` / `_contract_pool_filter` | `obligations.py` / `sp/formulation.py` | 原样迁 + 测试随迁 |
| `sp_solve_lp` / `sp_solve_ip` 公式段 | `sp/formulation.py` | 循环/编排留在母项目 `column_generate`（→ OptiCore） |
| `price_columns` 的 rc 公式与合法域剪枝 | `sp/pricing.py` 定义 | 贪心搜索循环 → OptiCore |
| `tsp_engine` 的 CP-SAT 公式与凭证契约 | `tsp/open_chain.py` | NN+2opt/LKH → OptiCore |
| `algos/r2_alns.move_candidates`（合同合法域生成） | `obligations.py` 邻域规格 | 搜索执行 → OptiCore |

## 4. 抽离就绪要求（母项目 Task 2/3 编码时即遵守）

1. 模型构建器不 import algos/*（只 import visit_ir + ortools）；
2. 循环/编排不内联公式（调 VisitModel 构建器）；
3. 测试三件套随迁：部件单元 / 暴力枚举可行集等价 / 结构保证。

## 5. 待定

- SP 对偶价在合同模式下的语义命名（店覆盖影子价 vs 义务覆盖影子价）；
- 多求解器绑定的目录形态（ortools/ 子包 or 绑定层）；
- ObligationSet 尚在 VisitIR v0.1 定义中——降级 API 等其冻结后定型。
