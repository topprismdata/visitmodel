"""VisitModel — 访问调度数学公式层 (SP 公式/定价/TSP 开链公式); 母项目为其消费者.

运行期依赖: visit_ir (合同语义) + ortools (GLOP/CP-SAT) + opticore (纯几何原语).

三层定位 (docs/design/THREE_LAYER_ARCHITECTURE_v0.2.md): 本包 = Layer 2
Mathematical Compiler — MathCompiler 把 VisitSemanticSpec 确定性翻译成
VisitPlanningInstance, MathValidator 独立验解 (Solver 不自证合法).
"""
__version__ = "0.1.0"

from visitmodel.sp.formulation import (_wd, weekday_dates, _z_open, _contract_pool_filter,  # noqa: F401
                                       _fw_table, sp_solve_lp, sp_solve_ip)
from visitmodel.sp.pricing import price_columns  # noqa: F401
from visitmodel.tsp.open_chain import (_exact_open_tsp, _exact_open_tsp_status,  # noqa: F401
                                       TSPEngine, ExactTSPEngine, NN2OptEngine,
                                       ENGINES, get_engine)
from visitmodel.compiler import CONSTRAINT_IDS, MathCompiler  # noqa: F401
from visitmodel.validator import MathValidator  # noqa: F401
