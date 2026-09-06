# -*- coding: utf-8 -*-
"""Re-export surface: moved builders importable from visitmodel + behavior spot checks."""
from datetime import date

from visitmodel import (_wd, weekday_dates, _z_open, _contract_pool_filter,
                        sp_solve_lp, sp_solve_ip, price_columns)
from visitmodel.tsp.open_chain import _exact_open_tsp_status, _nn2opt_open


def test_wd_and_weekday_dates():
    assert _wd(date(2026, 7, 6)) == 0            # 周一
    assert _wd("2026-07-08") == 2                # ISO 字符串分支
    g = weekday_dates([date(2026, 7, 6), date(2026, 7, 8), date(2026, 7, 13)])
    assert set(g) == {0, 2} and len(g[0]) == 2


def test_z_open_prunes_by_slot_count():
    assert _z_open({0: 4, 1: 2}, {0: [1, 2], 2: [3]}) == {0: [], 1: [0]}


def test_contract_pool_filter_generic_legal_domain():
    d1, d2 = date(2026, 7, 6), date(2026, 7, 8)
    pool = [(d1, [0], 1.0), (d2, [0], 1.0), (d1, [0, 1], 2.0)]
    out, drop = _contract_pool_filter(pool, {0: {d1}, 1: {d2}})
    assert drop == 2 and out == [(d1, [0], 1.0)]


def test_tsp_trivial_branch_and_heuristic_fallback():
    route, status, ms = _exact_open_tsp_status([0, 1, 2], [[0.0]], 1)
    assert (route, status, ms) == ([0, 1, 2], "TRIVIAL", 0.0)
    D = [[abs(i - j) for j in range(5)] for i in range(5)]
    assert _nn2opt_open([0, 1, 2, 3, 4], D) == [0, 1, 2, 3, 4]
