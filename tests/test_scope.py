"""Scope enforcer tests."""

from __future__ import annotations

from agent_evolve.models import ScopeSpec
from agent_evolve.scope import enforce_scope


def test_in_scope_files_pass():
    scope = ScopeSpec(target_files=["src/pricing/calculator.py"])
    r = enforce_scope(["src/pricing/calculator.py"], scope)
    assert r.in_scope
    assert r.violations == []


def test_out_of_scope_file_rejected():
    scope = ScopeSpec(target_files=["src/pricing/calculator.py"])
    r = enforce_scope(["src/api/routes.py"], scope)
    assert not r.in_scope
    assert "src/api/routes.py" in r.out_of_scope_files


def test_do_not_touch_overrides_target_files():
    scope = ScopeSpec(
        target_files=["src/pricing/**"],
        do_not_touch=["src/pricing/models.py"],
    )
    r = enforce_scope(["src/pricing/models.py"], scope)
    assert not r.in_scope
    assert "src/pricing/models.py" in r.protected_files


def test_directory_pattern_matches_recursive():
    scope = ScopeSpec(target_files=["src/pricing/"])
    r = enforce_scope(
        ["src/pricing/calculator.py", "src/pricing/sub/helper.py"],
        scope,
    )
    assert r.in_scope


def test_do_not_touch_directory_pattern():
    scope = ScopeSpec(
        target_files=["**"],
        do_not_touch=["src/auth/"],
    )
    r = enforce_scope(["src/auth/login.py", "src/auth/deep/nested.py"], scope)
    assert not r.in_scope
    assert r.protected_files == ["src/auth/login.py", "src/auth/deep/nested.py"]


def test_max_diff_files_enforced():
    scope = ScopeSpec(target_files=["**"], max_diff_files=2)
    r = enforce_scope(["a.py", "b.py", "c.py"], scope)
    assert not r.in_scope
    assert r.too_many_files


def test_windows_path_normalized():
    scope = ScopeSpec(target_files=["src/pricing/calculator.py"])
    r = enforce_scope([r"src\pricing\calculator.py"], scope)
    assert r.in_scope


def test_empty_diff_is_in_scope():
    scope = ScopeSpec(target_files=["src/pricing/**"])
    r = enforce_scope([], scope)
    assert r.in_scope


def test_mixed_violations_reported_separately():
    scope = ScopeSpec(
        target_files=["src/pricing/calculator.py"],
        do_not_touch=["src/pricing/models.py"],
    )
    r = enforce_scope(
        ["src/pricing/calculator.py", "src/pricing/models.py", "src/api/routes.py"],
        scope,
    )
    assert not r.in_scope
    assert "src/pricing/models.py" in r.protected_files
    assert "src/api/routes.py" in r.out_of_scope_files
