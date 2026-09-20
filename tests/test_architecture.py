# -*- coding: utf-8 -*-
"""VisitModel 仓架构守卫: API 纯类型零依赖 + L2 实现层纪律.

> 出处: docs/design/THREE_LAYER_ARCHITECTURE_v0.2.md §3.0 (GPT P0-3).

- visit_math_api/**: 纯类型包, 除标准库禁止一切 import;
- visitmodel/compiler.py + validator.py (Phase A 新增):
  禁 import algos / ortools (验解独立于后端) / visit_ir 实现自由函数;
  允许 visit_semantic_api (消费 L1 类型) + visit_math_api;
- 既有 sp/ tsp/ 子模块不在本守卫范围 (历史迁移, Phase C 处理).
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

_STDLIB_ALLOWED = True  # visit_math_api 只允许标准库


def _imports_of(py_file: Path) -> set:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def test_visit_math_api_is_pure_types():
    import sys
    stdlib = set(sys.stdlib_module_names)
    api_dir = SRC / "visit_math_api"
    for py in sorted(api_dir.rglob("*.py")):
        mods = _imports_of(py)
        third = {m for m in mods if m not in stdlib}
        assert not third, f"visit_math_api 只许标准库, {py.name} import 了 {sorted(third)}"


def test_compiler_validator_discipline():
    """Phase A 新增的 L2 编译/验解模块: 不碰算法层/求解器/语义自由函数."""
    forbidden_roots = {"algos", "ortools", "numpy", "pandas"}
    banned_names = {"contract_of", "legal_date_map", "check_contract", "check_rhythm",
                    "contract_slot_dates", "LineData", "days_orig"}
    banned_attrs = {"LineData", "days_orig"}  # .contract_of 是 Spec 合法接口
    for name in ("compiler.py", "validator.py"):
        py = SRC / "visitmodel" / name
        assert py.exists(), name
        mods = _imports_of(py)
        bad = mods & forbidden_roots | mods & {"visit_ir"}
        assert not bad, f"{name} 违禁 import: {sorted(bad)}"
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in banned_names:
                pytest.fail(f"{name}:{node.lineno} 裸引用语义标识符: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in banned_attrs:
                pytest.fail(f"{name}:{node.lineno} 引用语义属性: {node.attr}")


def test_no_shadow_packages():
    """visit_semantic_api 属 VisitIR 仓 — 本仓不得 shadow."""
    assert not (SRC / "visit_semantic_api").exists()
