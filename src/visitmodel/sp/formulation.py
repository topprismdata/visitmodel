# -*- coding: utf-8 -*-
"""SP 集合划分公式构建器: LP (GLOP) / IP (CP-SAT) + R2' z 线性化 + 合同池过滤 + 对偶语义.

原样迁自母项目 algos/sp_matheuristic.py (extraction sprint; 蓝图口径: solver-agnostic
结构 + ortools 绑定). 构建器只依赖 visit_ir + ortools, 以参数接收合同/池/走廊;
编排循环 (column_generate) 与算法类 (SPMatheuristic) 留在母项目.
"""
import datetime as _dt

from visit_ir.contract import legal_date_map


def _wd(date):
    return date.weekday() if hasattr(date, "weekday") else _dt.date.fromisoformat(str(date)).weekday()


def weekday_dates(dates):
    g = {}
    for dd in dates:
        g.setdefault(_wd(dd), []).append(dd)
    return g


def _z_open(k_c, wd_groups):
    """R2' 可行星期几集: 仅槽位数 ≥ f_c 的 w 对店 c 开放 (频次-槽位预剪枝)."""
    return {c: [w for w, ds in wd_groups.items() if f <= len(ds)] for c, f in k_c.items()}


def _contract_pool_filter(pool, legal):
    """合同池过滤: 剔除任何 (店,日期) 非法成员关系的列. 返回 (pool, n_dropped)."""
    out, drop = [], 0
    for date, route, km in pool:
        if any(c in legal and date not in legal[c] for c in route):
            drop += 1
            continue
        out.append((date, route, km))
    return out, drop


def _fw_table(contracts, wd_groups):
    """派生频次表 {c: {w: f(c,w)}}: f = |合同槽位集| (周访=k_w, 双周=相位计数)."""
    from visit_ir.contract import contract_slot_dates
    return {c: {w: len(contract_slot_dates(k, p, ds)) for w, ds in wd_groups.items()}
            for c, (k, p) in contracts.items()}


def sp_solve_lp(dates, k_c, pool, timeout_s=60, r2_prime=False, contract=None,
                legal=None, fw=None):
    """受限主问题 LP 值 (GLOP). 返回 (rmp_lp, duals) 或 (None, None).
    合同语义两种供给 (Phase D1/D2 拆分):
    - contract= 原始合同字典 (legacy; 内部经 visit_ir 翻译, 迁移期保留);
    - legal=+fw= 编译视图 (L1 派生, orchestration.contract_view) — 首选;
      view 模式下覆盖走 z 线性化 (等价 contract 模式).
    """
    from ortools.linear_solver import pywraplp
    if contract is not None:
        if legal is None:
            legal = legal_date_map(contract, dates)
        if fw is None:
            fw = _fw_table(contract, weekday_dates(dates))
    view = contract is not None or (legal is not None and fw is not None)
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        return None, None
    if legal is not None:
        pool, _ = _contract_pool_filter(pool, legal)
    x = {i: solver.NumVar(0, 1, f"x{i}") for i in range(len(pool))}
    cons_date, cons_store = {}, {}
    for dd in dates:
        cols = [i for i, (date, _, _) in enumerate(pool) if date == dd]
        if not cols:
            return None, None
        cons_date[dd] = solver.Add(sum(x[i] for i in cols) == 1)
    for c, k in k_c.items():
        if view:
            continue          # 合同模式: 覆盖等式在 z 线性化段统一施加
        cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
        if cols:
            cons_store[c] = solver.Add(sum(x[i] for i in cols) == k)
    z = {}
    if r2_prime or view:
        wd_g = weekday_dates(dates)
        for c in k_c:
            # contract+r2_prime 组合: 合同分支优先; z 绑定仍强制单一星期几, 相位合法性由池过滤保证
            ws = list(wd_g) if view else \
                [w for w, ds in wd_g.items() if k_c[c] <= len(ds)]
            if not ws:
                return None, None
            zc = {w: solver.NumVar(0, 1, f"z_{c}_{w}") for w in ws}
            solver.Add(sum(zc.values()) == 1)
            z[c] = zc
            if view:
                cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
                if not cols:
                    # 合同义务在池过滤后零合法列 = 不可行 (过滤可饿死店), 不是可跳过的约束
                    return None, None
                # 对偶语义: 该店合同覆盖的影子价格
                cons_store[c] = solver.Add(
                    sum(x[i] for i in cols) == sum(fw[c][w] * zc[w] for w in ws))
        for i, (date, route, _) in enumerate(pool):
            w = _wd(date)
            for c in set(route):
                if c in z and w in z[c]:
                    solver.Add(x[i] - z[c][w] <= 0)
    solver.Minimize(sum(pool[i][2] * x[i] for i in x))
    solver.SetTimeLimit(int(timeout_s * 1000))
    st = solver.Solve()
    if st not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return None, None
    duals = {"store": {c: cons_store[c].DualValue() for c in cons_store},
             "date": {dd: cons_date[dd].DualValue() for dd in cons_date}}
    return solver.Objective().Value(), duals
def sp_solve_ip(dates, k_c, pool, timeout_s=120, r2_prime=False, contract=None,
                legal=None, fw=None, return_diagnostics=False):
    """Solve the restricted SP integer problem with CP-SAT.

    The historical two-value return is preserved by default. With
    return_diagnostics=True the function returns (objective_km, days, info),
    including solver status, objective, bound, and pool size.
    """
    from ortools.sat.python import cp_model

    if contract is not None:
        if legal is None:
            legal = legal_date_map(contract, dates)
        if fw is None:
            fw = _fw_table(contract, weekday_dates(dates))
    view = contract is not None or (legal is not None and fw is not None)
    dropped = 0
    if legal is not None:
        pool, dropped = _contract_pool_filter(pool, legal)
    diagnostics = {
        "status": None,
        "solver_status": None,
        "optimality_proven": False,
        "objective_value_milli": None,
        "best_bound_milli": None,
        "time_limit_s": float(timeout_s),
        "wall_time_s": None,
        "pool_size": len(pool),
        "contract_pool_dropped": int(dropped),
    }

    def finish(km, days, status):
        diagnostics["status"] = status
        if diagnostics["solver_status"] is None:
            diagnostics["solver_status"] = status
        if return_diagnostics:
            return km, days, diagnostics
        return km, days

    m = cp_model.CpModel()
    xv = {}
    for idx, (date, route, km) in enumerate(pool):
        xv[idx] = m.NewBoolVar(f"x{idx}")
    for dd in dates:
        cols = [i for i, (date, _, _) in enumerate(pool) if date == dd]
        if not cols:
            return finish(None, None, "PRECHECK_INFEASIBLE")
        m.AddExactlyOne([xv[i] for i in cols])
    for c, k in k_c.items():
        if view:
            continue          # 合同模式: 覆盖等式在 z 线性化段统一施加
        cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
        if cols:
            m.Add(sum(xv[i] for i in cols) == k)
    z = {}
    if r2_prime or view:
        wd_g = weekday_dates(dates)
        for c in k_c:
            # contract+r2_prime 组合: 合同分支优先; z 绑定仍强制单一星期几, 相位合法性由池过滤保证
            ws = list(wd_g) if view else \
                [w for w, ds in wd_g.items() if k_c[c] <= len(ds)]
            if not ws:
                return finish(None, None, "PRECHECK_INFEASIBLE")
            zc = {w: m.NewBoolVar(f"z_{c}_{w}") for w in ws}
            m.AddExactlyOne(list(zc.values()))
            z[c] = zc
            if view:
                cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
                if not cols:
                    # 合同义务在池过滤后零合法列 = 不可行 (过滤可饿死店), 不是可跳过的约束
                    return finish(None, None, "PRECHECK_INFEASIBLE")
                # 合同覆盖线性化: 覆盖数 == 所选星期几的合同槽位数 f(c,w)
                m.Add(sum(xv[i] for i in cols) == sum(fw[c][w] * zc[w] for w in ws))
        for i, (date, route, _) in enumerate(pool):
            w = _wd(date)
            for c in set(route):
                if c in z and w in z[c]:
                    m.Add(xv[i] <= z[c][w])
    m.Minimize(sum(int(round(pool[i][2] * 1000)) * xv[i] for i in xv))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_s
    solver.parameters.num_search_workers = 8
    st = solver.Solve(m)
    status_name = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(st, f"STATUS_{st}")
    diagnostics.update({
        "solver_status": status_name,
        "optimality_proven": st == cp_model.OPTIMAL,
        "wall_time_s": float(solver.WallTime()),
    })
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        diagnostics["objective_value_milli"] = float(solver.ObjectiveValue())
        diagnostics["best_bound_milli"] = float(solver.BestObjectiveBound())
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return finish(None, None, status_name)
    sel = {dd: None for dd in dates}
    for i, (date, route, km) in enumerate(pool):
        if solver.Value(xv[i]):
            cur = sel[date]
            if cur is None or km < cur[1]:
                sel[date] = (list(route), km)
    if any(v is None for v in sel.values()):
        return finish(None, None, "INCOMPLETE_SOLUTION")
    diagnostics["selected_objective_milli"] = sum(
        int(round(v[1] * 1000)) for v in sel.values()
    )
    km_pool = sum(v[1] for v in sel.values())
    return finish(km_pool, {dd: v[0] for dd, v in sel.items()}, status_name)


# ---------------------------------------------------------------------------
# vNext linkage formulation (v2): 固定 (店,日期) 行空间
# 设计依据: 母仓 docs/design/FORMULATION_VNEXT_DESIGN_v0.3.md §3
# 缺口修复: v1 的 x_r ≤ z_{c,wd(r)} 为列特定行——新列同时新增行, 行空间不固定,
# 且定价 rc 未计入绑定行对偶, 不构成标准 fixed-row Column Generation。
# ---------------------------------------------------------------------------


def linkage_domain(dates, k_c, contract):
    """R2′ 域: 正频次星期域 W⁺ 与 (店,日期) 合法域。

    返回 (legal, fw, w_plus):
      legal   {c: set(dates)}      合同合法日期集 (VisitIR 同源)
      fw      {c: {w: f_cw}}       各星期几的合同频次 (与合法域同版本)
      w_plus  {c: [w,...]}         正频次星期域 (f_cw > 0)

    任一店 w_plus 为空 → 该店当月不可服务, 调用方必须返回明确不可行,
    不得静默跳过 (零频次星期几选 z 会让整店消失, 见评审 R2 反例)。
    """
    from visit_ir.contract import contract_slot_dates, legal_date_map
    legal = legal_date_map(contract, dates)
    wd_g = weekday_dates(dates)
    fw = {c: {w: len(contract_slot_dates(k, ph, ds)) for w, ds in wd_g.items()}
          for c, (k, ph) in contract.items()}
    w_plus = {c: sorted(w for w, f in fwc.items() if f > 0) for c, fwc in fw.items()}
    return legal, fw, w_plus


def sp_solve_lp_v2(dates, k_c, pool, contract, timeout_s=60):
    """vNext 固定 (店,日期) 行空间受限主问题 LP (GLOP)。行数与池大小无关。

    返回 (obj, duals) 或 (None, None)。duals = {
        'date': {d: π_d},                      # (A) 日期覆盖行
        'link': {(c, d): β_cd},                # (B) 店-日链接行
    }
    定价 (代数定义, 非约定): 列 r 的系数 — (A) 为 +1, (B) 为 -1, 故
        rc(r@d) = km_r − π_d + Σ_{c∈r} β_cd     （reward_cd = −β_cd）
    符号由测试钉死: 收敛点池内列 rc 必须 ≥ 0 (下界态), 上界态 ≤ 0。

    特性:
      - 行空间固定 → 标准 fixed-row Column Generation;
      - 合法域内生化 (非法 (c,d) 的 y 上界为 0), 不依赖池预过滤;
      - z 仅定义于正频次星期域 W⁺, 空集返回不可行 (杜绝零频次整店消失)。
    """
    from ortools.linear_solver import pywraplp
    legal, fw, w_plus = linkage_domain(dates, k_c, contract)
    for c in k_c:
        if not w_plus[c]:
            return None, None          # 该店当月无任何可服务星期 → 明确不可行
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        return None, None
    x = {i: solver.NumVar(0, 1, f"x{i}") for i in range(len(pool))}
    y = {}
    for c in k_c:
        for dd in dates:
            ub = 1 if dd in legal.get(c, ()) else 0
            y[(c, dd)] = solver.NumVar(0, ub, f"y_{c}_{dd}")
    z = {c: {w: solver.NumVar(0, 1, f"z_{c}_{w}") for w in w_plus[c]}
         for c in k_c}
    # (A) 日期覆盖
    cons_date = {}
    for dd in dates:
        cols = [i for i, (d2, _, _) in enumerate(pool) if d2 == dd]
        if not cols:
            return None, None
        cons_date[dd] = solver.Add(sum(x[i] for i in cols) == 1)
    # (B) 店-日链接 (固定行)
    cons_link = {}
    members = {}
    for i, (dd, route, _) in enumerate(pool):
        for c in set(route):
            members.setdefault((c, dd), []).append(i)
    for c in k_c:
        for dd in dates:
            cons_link[(c, dd)] = solver.Add(
                y[(c, dd)] - sum(x[i] for i in members.get((c, dd), [])) == 0)
    # (C) 星期几绑定 + (D) 合同频次 (经 y 计数) + (E) 唯一星期几
    for c in k_c:
        for dd in dates:
            w = _wd(dd)
            if w in z[c]:
                solver.Add(y[(c, dd)] <= z[c][w])
        solver.Add(sum(y[(c, dd)] for dd in dates)
                   == sum(fw[c][w] * z[c][w] for w in w_plus[c]))
        solver.Add(sum(z[c].values()) == 1)
    solver.Minimize(sum(pool[i][2] * x[i] for i in x))
    solver.SetTimeLimit(int(timeout_s * 1000))
    st = solver.Solve()
    if st != pywraplp.Solver.OPTIMAL:
        return None, None
    duals = {"date": {dd: cons_date[dd].DualValue() for dd in cons_date},
             "link": {k: cons_link[k].DualValue() for k in cons_link}}
    return solver.Objective().Value(), duals


def sp_solve_ip_v2(dates, k_c, pool, contract, timeout_s=120):
    """vNext 固定行空间 SP 整数精确解 (CP-SAT)。

    返回 diagnostics dict (含 status / objective_value_milli / selected days)。
    行语义与 sp_solve_lp_v2 完全一致; 合法域由 y 上界内生化。
    """
    from ortools.sat.python import cp_model
    legal, fw, w_plus = linkage_domain(dates, k_c, contract)
    for c in k_c:
        if not w_plus[c]:
            return {"status": "INFEASIBLE", "formulation": "linkage_v2"}
    m = cp_model.CpModel()
    x = {i: m.NewBoolVar(f"x{i}") for i in range(len(pool))}
    y = {}
    for c in k_c:
        for dd in dates:
            ub = 1 if dd in legal.get(c, ()) else 0
            y[(c, dd)] = m.NewIntVar(0, ub, f"y_{c}_{dd}")
    z = {c: {w: m.NewBoolVar(f"z_{c}_{w}") for w in w_plus[c]} for c in k_c}
    for dd in dates:
        cols = [i for i, (d2, _, _) in enumerate(pool) if d2 == dd]
        if not cols:
            return {"status": "PRECHECK_INFEASIBLE",
                    "formulation": "linkage_v2"}
        m.AddExactlyOne([x[i] for i in cols])
    members = {}
    for i, (dd, route, _) in enumerate(pool):
        for c in set(route):
            members.setdefault((c, dd), []).append(i)
    for c in k_c:
        for dd in dates:
            m.Add(y[(c, dd)] == sum(x[i] for i in members.get((c, dd), [])))
        for dd in dates:
            w = _wd(dd)
            if w in z[c]:
                m.Add(y[(c, dd)] <= z[c][w])
        m.Add(sum(y[(c, dd)] for dd in dates)
              == sum(fw[c][w] * z[c][w] for w in w_plus[c]))
        m.AddExactlyOne(list(z[c].values()))
    m.Minimize(sum(int(round(pool[i][2] * 1000)) * x[i] for i in x))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_s
    solver.parameters.num_search_workers = 8
    st = solver.Solve(m)
    status_name = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.UNKNOWN: "UNKNOWN",
    }.get(st, f"STATUS_{st}")
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": status_name, "formulation": "linkage_v2"}
    sel = {}
    for dd in dates:
        best = None
        for i, (d2, route, km) in enumerate(pool):
            if d2 == dd and solver.Value(x[i]):
                if best is None or km < best[1]:
                    best = (list(route), km)
        sel[dd] = best
    if any(v is None for v in sel.values()):
        return {"status": "INCOMPLETE_SOLUTION", "formulation": "linkage_v2"}
    km_pool = sum(v[1] for v in sel.values())
    return {"status": status_name, "km_pool": km_pool,
            "days": {dd: v[0] for dd, v in sel.items()},
            "formulation": "linkage_v2"}
