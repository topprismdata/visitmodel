# -*- coding: utf-8 -*-
"""SP 定价子问题: rc 公式 + 合法域剪枝 + 支配截断 + 双向走廊硬截断.

原样迁自母项目 algos/sp_matheuristic.price_columns. `legal` 为通用 店->合法日期集
映射 (无领域词汇); 贪心构造循环随迁 (蓝图: 定价定义归 VisitModel).
"""


def day_km(seq, D):
    """Open-chain route distance over store indices (与母项目 core.metric.day_km 同义同实现)."""
    if len(seq) < 2:
        return 0.0
    return float(sum(D[seq[k]][seq[k+1]] for k in range(len(seq) - 1)))


def price_columns(dates, k_c, duals, D, candidates_per_date=24, top_m=40, col_iter=60,
                  max_daily=None, min_daily=None, legal=None):
    """定价子问题 (启发式, [ESF] §7.1 批量定价 + 支配剪枝 + 走廊硬截断).
    rc = km(r) - Σ u_c - w_d. 能力边界: 只报告"发现"的负列, 不证明不存在其他负列."""
    u = duals["store"]; w = duals["date"]
    all_stores = sorted(k_c.keys(), key=lambda c: -u.get(c, 0.0))[:top_m]
    cands = []
    for dd in dates:
        w_d = w.get(dd, 0.0)
        for start_c in all_stores:
            if legal is not None and dd not in legal.get(start_c, ()):
                continue
            if u.get(start_c, 0.0) <= 0:
                break
            route = [start_c]
            in_day = {start_c}
            while True:
                if max_daily is not None and len(route) >= max_daily:
                    break
                best_c, best_margin, best_pos = None, 1e-9, None
                for c in all_stores:
                    if c in in_day:
                        continue
                    if legal is not None and dd not in legal.get(c, ()):
                        continue
                    uc = u.get(c, 0.0)
                    if uc <= best_margin:
                        break
                    bd, bp = D[c][route[0]], 0
                    for k in range(len(route) - 1):
                        dlt = D[route[k]][c] + D[c][route[k+1]] - D[route[k]][route[k+1]]
                        if dlt < bd:
                            bd, bp = dlt, k + 1
                    d_last = D[route[-1]][c]
                    if d_last < bd:
                        bd, bp = d_last, len(route)
                    margin = uc - bd
                    if margin > best_margin:
                        best_c, best_margin, best_pos = c, margin, bp
                if best_c is None:
                    break
                route.insert(best_pos, best_c)
                in_day.add(best_c)
            rc = day_km(route, D) - sum(u.get(c, 0.0) for c in route) - w_d
            lo = max(2, min_daily or 2)
            if rc < -1e-6 and len(route) >= lo:
                cands.append((rc, dd, list(route), round(day_km(route, D), 3)))
    cands.sort(key=lambda z0: z0[0])
    return [(dd, route, km) for rc, dd, route, km in cands[:col_iter]]
