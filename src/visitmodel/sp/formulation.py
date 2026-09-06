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


def sp_solve_lp(dates, k_c, pool, timeout_s=60, r2_prime=False, contract=None):
    """受限主问题 LP 值 (GLOP). 返回 (rmp_lp, duals) 或 (None, None).
    contract 非 None: 合同模式 — 池预过滤 + 覆盖 RHS 线性化 (sum x == sum f·z, 取代 ==k),
    z 开放全部星期几; store 对偶 = 该店合同覆盖约束的影子价格."""
    from ortools.linear_solver import pywraplp
    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        return None, None
    if contract is not None:
        pool, _ = _contract_pool_filter(pool, legal_date_map(contract, dates))
    x = {i: solver.NumVar(0, 1, f"x{i}") for i in range(len(pool))}
    cons_date, cons_store = {}, {}
    for dd in dates:
        cols = [i for i, (date, _, _) in enumerate(pool) if date == dd]
        if not cols:
            return None, None
        cons_date[dd] = solver.Add(sum(x[i] for i in cols) == 1)
    for c, k in k_c.items():
        if contract is not None:
            continue          # 合同模式: 覆盖等式在 z 线性化段统一施加
        cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
        if cols:
            cons_store[c] = solver.Add(sum(x[i] for i in cols) == k)
    z = {}
    if r2_prime or contract is not None:
        wd_g = weekday_dates(dates)
        fw = _fw_table(contract, wd_g) if contract is not None else None
        for c in k_c:
            # contract+r2_prime 组合: 合同分支优先; z 绑定仍强制单一星期几, 相位合法性由池过滤保证
            ws = list(wd_g) if contract is not None else \
                [w for w, ds in wd_g.items() if k_c[c] <= len(ds)]
            if not ws:
                return None, None
            zc = {w: solver.NumVar(0, 1, f"z_{c}_{w}") for w in ws}
            solver.Add(sum(zc.values()) == 1)
            z[c] = zc
            if contract is not None:
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


def sp_solve_ip(dates, k_c, pool, timeout_s=120, r2_prime=False, contract=None):
    """SP 整数精确解 (CP-SAT). r2_prime=True 时施加每店单一星期几硬约束.
    contract 非 None: 合同模式 — 池预过滤 + 覆盖 RHS 线性化 (sum x == sum f·z, 取代 ==k),
    z 开放全部星期几 (线性化自剪枝)."""
    from ortools.sat.python import cp_model
    if contract is not None:
        pool, _ = _contract_pool_filter(pool, legal_date_map(contract, dates))
    m = cp_model.CpModel()
    xv = {}
    for idx, (date, route, km) in enumerate(pool):
        xv[idx] = m.NewBoolVar(f"x{idx}")
    for dd in dates:
        cols = [i for i, (date, _, _) in enumerate(pool) if date == dd]
        if not cols:
            return None, None
        m.AddExactlyOne([xv[i] for i in cols])
    for c, k in k_c.items():
        if contract is not None:
            continue          # 合同模式: 覆盖等式在 z 线性化段统一施加
        cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
        if cols:
            m.Add(sum(xv[i] for i in cols) == k)
    z = {}
    if r2_prime or contract is not None:
        wd_g = weekday_dates(dates)
        fw = _fw_table(contract, wd_g) if contract is not None else None
        for c in k_c:
            # contract+r2_prime 组合: 合同分支优先; z 绑定仍强制单一星期几, 相位合法性由池过滤保证
            ws = list(wd_g) if contract is not None else \
                [w for w, ds in wd_g.items() if k_c[c] <= len(ds)]
            if not ws:
                return None, None
            zc = {w: m.NewBoolVar(f"z_{c}_{w}") for w in ws}
            m.AddExactlyOne(list(zc.values()))
            z[c] = zc
            if contract is not None:
                cols = [i for i, (_, route, _) in enumerate(pool) if c in route]
                if not cols:
                    # 合同义务在池过滤后零合法列 = 不可行 (过滤可饿死店), 不是可跳过的约束
                    return None, None
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
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None
    sel = {dd: None for dd in dates}
    for i, (date, route, km) in enumerate(pool):
        if solver.Value(xv[i]):
            cur = sel[date]
            if cur is None or km < cur[1]:
                sel[date] = (list(route), km)
    if any(v is None for v in sel.values()):
        return None, None
    return sum(v[1] for v in sel.values()), {dd: v[0] for dd, v in sel.items()}
