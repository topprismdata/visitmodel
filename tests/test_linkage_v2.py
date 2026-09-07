# -*- coding: utf-8 -*-
"""vNext linkage formulation (v2) 守卫测试.

设计依据: 母仓 docs/design/FORMULATION_VNEXT_DESIGN_v0.3/v0.4.md
钉死: IP 等价 / LP 界方向 (v2 ≥ v1) / 合法域内生化 / 零频次反例 / CG 全枚举真值。
"""
import itertools
from datetime import date

import numpy as np

from visit_ir.contract import contract_of, legal_date_map, check_contract
from visitmodel.sp.formulation import (
    linkage_domain,
    sp_solve_lp,
    sp_solve_lp_v2,
    sp_solve_ip,
    sp_solve_ip_v2,
)
from visitmodel.sp.pricing import price_columns_v2

# 4 工作日: 两周一(W1/W3) + 两周二(W2/W4), phase parity 交错
DATES = [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 13), date(2026, 7, 14)]
MON, TUE, MON2, TUE2 = DATES

# 合法计划: 店0 周一(W1/W3), 店1 周二(W2/W4), 店2 双周偶周一(W1), 店3 双周周二(W2)
DAYS_ORIG = {MON: [0, 2], TUE: [1, 3], MON2: [0, 2], TUE2: [1, 3]}
CONTRACTS = contract_of(DAYS_ORIG, DATES)
K_C = {c: 2 for c in range(4)}

# 对称距离: 4 店环 0-1-2-3-0, 相邻 1.0
_D = np.full((4, 4), 10.0)
for i in range(4):
    _D[i][(i + 1) % 4] = 1.0
    _D[(i + 1) % 4][i] = 1.0


def _km(route):
    return round(sum(_D[route[k]][route[k + 1]] for k in range(len(route) - 1)), 3)


def _legal_columns():
    """全列枚举: 每日期合法店的全部 1~2 店子集 (字典序路线)."""
    legal = legal_date_map(CONTRACTS, DATES)
    out = []
    for dd in DATES:
        legal_c = sorted(c for c in range(4) if dd in legal.get(c, ()))
        for size in (1, 2):
            for subset in itertools.combinations(legal_c, size):
                out.append((dd, list(subset), _km(subset)))
    return out


def _plan_pool():
    """原计划列 (保证可行) + 每日期丢一子集列 (合法、走廊内)."""
    pool = []
    for dd in DATES:
        base_route = sorted(DAYS_ORIG[dd])
        pool.append((dd, base_route, _km(base_route)))
        for drop in base_route:
            sub = [x for x in base_route if x != drop]
            if sub:
                pool.append((dd, sub, _km(sub)))
    return pool


# ----- R2 反例回归: 双周店不可因零频次星期几被静默跳过 -----

def test_v2_ip_selects_all_stores_and_respects_phase():
    dates = [MON, TUE]
    days_orig = {MON: [0, 2], TUE: [1]}
    contracts = contract_of(days_orig, dates)
    k_c = {0: 1, 1: 1, 2: 1}
    pool = [
        (MON, [0], 10.0),
        (MON, [0, 2], 25.0),
        (TUE, [1], 5.0),
    ]
    res = sp_solve_ip_v2(dates, k_c, pool, contracts, timeout_s=30)
    assert res["status"] == "OPTIMAL"
    served = {c for route in res["days"].values() for c in route}
    assert 2 in served, "双周店 2 被静默跳过 = 零频次漏洞回归"
    assert len(check_contract(res["days"], contracts, dates)) == 0


# ----- T3: 同一冻结合法列池, v1 IP 与 v2 IP 最优值相等 -----

def test_v1_v2_ip_equivalence_on_frozen_pools():
    from visitmodel.sp.formulation import sp_solve_ip
    rng = np.random.default_rng(7)
    base = _plan_pool()
    for _trial in range(4):
        pool = list(base)
        for dd in DATES:
            dd_cols = [c for c in base if c[0] == dd]
            idx = rng.choice(len(dd_cols), size=min(3, len(dd_cols)), replace=False)
            pool += [dd_cols[i] for i in sorted(idx)]
        km1, _days1, info1 = sp_solve_ip(DATES, K_C, pool, 60, r2_prime=True,
                                         contract=CONTRACTS, return_diagnostics=True)
        res2 = sp_solve_ip_v2(DATES, K_C, pool, CONTRACTS, timeout_s=60)
        assert info1["status"] == "OPTIMAL", info1["status"]
        assert res2["status"] == "OPTIMAL", res2["status"]
        assert abs(km1 - res2["km_pool"]) < 1e-6


# ----- T4: LP 界方向 (同池同合法域下 v2 LP ≥ v1 LP) -----

def test_v2_lp_bound_not_weaker_than_v1():
    from visitmodel.sp.formulation import sp_solve_lp
    rng = np.random.default_rng(11)
    base = _plan_pool()
    for _trial in range(3):
        pool = list(base)
        for dd in DATES:
            dd_cols = [c for c in base if c[0] == dd]
            idx = rng.choice(len(dd_cols), size=min(3, len(dd_cols)), replace=False)
            pool += [dd_cols[i] for i in sorted(idx)]
        lp1, _ = sp_solve_lp(DATES, K_C, pool, 30, r2_prime=True, contract=CONTRACTS)
        lp2, _ = sp_solve_lp_v2(DATES, K_C, pool, CONTRACTS, 30)
        assert lp1 is not None and lp2 is not None
        assert lp2 >= lp1 - 1e-6


# ----- T2: CG 真值对照 (微型实例全列枚举) -----

def test_cg_v2_converges_to_full_enumeration_truth():
    all_cols = _legal_columns()
    full_lp, _ = sp_solve_lp_v2(DATES, K_C, all_cols, CONTRACTS, 30)
    assert full_lp is not None

    pool = list(_plan_pool())
    legal = legal_date_map(CONTRACTS, DATES)
    for _round in range(10):
        lp, duals = sp_solve_lp_v2(DATES, K_C, pool, CONTRACTS, 30)
        assert lp is not None
        in_pool = {(dd, frozenset(rt)) for dd, rt, _ in pool}
        fresh = [(dd, rt, km) for dd, rt, km in price_columns_v2(
            DATES, K_C, duals["date"], duals["link"], _D,
            max_daily=2, min_daily=1, legal=legal)
            if (dd, frozenset(rt)) not in in_pool]
        if not fresh:
            break
        pool += fresh

    lp_final, _ = sp_solve_lp_v2(DATES, K_C, pool, CONTRACTS, 30)
    assert abs(lp_final - full_lp) < 1e-6, (lp_final, full_lp)
