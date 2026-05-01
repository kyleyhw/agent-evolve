"""Tests for :mod:`agent_evolve.artifact` — sibling-mode seeding."""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from agent_evolve.artifact import (
    SeedResult,
    SiblingSeedError,
    _find_unique_top_level_symbol,
    _rename_identifier_in_source,
    _to_pascal_case,
    expand_template,
    seed_sibling,
)
from agent_evolve.models import (
    BackendSpec,
    EvolutionSpec,
    Metric,
    OptimiseDirection,
    ProblemSpec,
    RuntimeModeSpec,
    SafetySpec,
    ScopeSpec,
    SiblingSpec,
)


# ---------------------------------------------------------------------------
# expand_template
# ---------------------------------------------------------------------------


def test_expand_template_substitutes_every_token():
    """All six documented tokens are substituted in one call."""
    result = expand_template(
        "{original}_{ProblemId}_{Date}_{original_stem}_{problem_id}_{date}",
        original_symbol="MLRegimeStrategy",
        original_stem="ml_regime_strategy",
        problem_id="multi_asset",
        today=date(2026, 4, 30),
    )
    assert result == (
        "MLRegimeStrategy_MultiAsset_20260430_ml_regime_strategy_multi_asset_2026_04_30"
    )


def test_expand_template_passes_unknown_tokens_through():
    """Unknown ``{foo}`` placeholders are left untouched for caller layering."""
    result = expand_template(
        "{original}_{custom}",
        original_symbol="Foo", original_stem="foo",
        problem_id="x", today=date(2026, 1, 1),
    )
    assert result == "Foo_{custom}"


def test_expand_template_default_pattern_for_symbol():
    """The default ``symbol_rename_pattern`` produces a valid identifier."""
    result = expand_template(
        SiblingSpec().symbol_rename_pattern,
        original_symbol="Strategy",
        original_stem="strategy",
        problem_id="multiasset",
        today=date(2026, 4, 30),
    )
    assert result == "StrategyMultiasset20260430"
    assert result.isidentifier()


def test_expand_template_default_pattern_for_file():
    """The default ``file_rename_pattern`` produces a valid Python module stem."""
    result = expand_template(
        SiblingSpec().file_rename_pattern,
        original_symbol="Strategy",
        original_stem="ml_regime_strategy",
        problem_id="multiasset",
        today=date(2026, 4, 30),
    )
    assert result == "ml_regime_strategy_multiasset_2026_04_30"


# ---------------------------------------------------------------------------
# _to_pascal_case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("multiasset", "Multiasset"),
        ("multi_asset", "MultiAsset"),
        ("multi-asset", "MultiAsset"),
        ("ml_regime_strategy", "MlRegimeStrategy"),
        ("MultiAsset", "MultiAsset"),
        ("multi asset", "MultiAsset"),
        ("", ""),
    ],
)
def test_to_pascal_case_handles_various_inputs(raw, expected):
    assert _to_pascal_case(raw) == expected


# ---------------------------------------------------------------------------
# _find_unique_top_level_symbol
# ---------------------------------------------------------------------------


def test_find_unique_top_level_symbol_finds_class():
    src = "class Strategy:\n    pass\n"
    assert _find_unique_top_level_symbol(src, Path("x.py")) == "Strategy"


def test_find_unique_top_level_symbol_finds_function():
    src = "def compute():\n    return 1\n"
    assert _find_unique_top_level_symbol(src, Path("x.py")) == "compute"


def test_find_unique_top_level_symbol_finds_async_function():
    src = "async def compute():\n    return 1\n"
    assert _find_unique_top_level_symbol(src, Path("x.py")) == "compute"


def test_find_unique_top_level_symbol_errors_on_no_symbols():
    src = "x = 1\ny = 2\n"
    with pytest.raises(SiblingSeedError, match="no top-level"):
        _find_unique_top_level_symbol(src, Path("x.py"))


def test_find_unique_top_level_symbol_errors_on_multiple_symbols():
    src = "class A:\n    pass\nclass B:\n    pass\n"
    with pytest.raises(SiblingSeedError, match="multiple top-level"):
        _find_unique_top_level_symbol(src, Path("x.py"))


def test_find_unique_top_level_symbol_ignores_nested_definitions():
    """A class containing methods still counts as a single top-level symbol."""
    src = textwrap.dedent("""
        class Strategy:
            def method_one(self): pass
            def method_two(self): pass
    """)
    assert _find_unique_top_level_symbol(src, Path("x.py")) == "Strategy"


# ---------------------------------------------------------------------------
# _rename_identifier_in_source
# ---------------------------------------------------------------------------


def test_rename_identifier_renames_class_definition_and_references():
    src = textwrap.dedent("""\
        class Strategy:
            pass

        s = Strategy()
        Strategy.factory()
    """)
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert "class NewStrategy:" in out
    assert "s = NewStrategy()" in out
    assert "NewStrategy.factory()" in out
    # Original ``Strategy`` (as a standalone identifier, not a substring of
    # ``NewStrategy``) should be gone. Match using a word-boundary regex
    # so ``NewStrategy`` does not falsely satisfy the absence check.
    import re
    assert re.search(r"\bStrategy\b", out) is None


def test_rename_identifier_renames_function_definition_and_calls():
    src = "def compute():\n    return 1\n\nresult = compute()\n"
    out = _rename_identifier_in_source(src, original="compute", new="compute_v2")
    assert "def compute_v2():" in out
    assert "result = compute_v2()" in out


def test_rename_identifier_preserves_strings_containing_the_name():
    """A string literal containing the symbol name is NOT rewritten."""
    src = textwrap.dedent("""\
        class Strategy:
            name = "Strategy"          # docstring/error messages keep the original
            def __repr__(self): return "Strategy()"
    """)
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert 'name = "Strategy"' in out
    assert 'return "Strategy()"' in out
    assert "class NewStrategy:" in out


def test_rename_identifier_preserves_comments_containing_the_name():
    src = textwrap.dedent("""\
        class Strategy:  # the Strategy class
            # Strategy notes here
            pass
    """)
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert "# the Strategy class" in out
    assert "# Strategy notes here" in out
    assert "class NewStrategy:" in out


def test_rename_identifier_does_not_match_substrings():
    """``Strategy`` does not match inside ``BaseStrategy`` or ``StrategyImpl``."""
    src = textwrap.dedent("""\
        class BaseStrategy: pass
        class StrategyImpl: pass
        class Strategy: pass
    """)
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert "class BaseStrategy: pass" in out
    assert "class StrategyImpl: pass" in out
    assert "class NewStrategy: pass" in out


def test_rename_identifier_handles_decorators_and_annotations():
    src = textwrap.dedent("""\
        @register
        class Strategy:
            registry: list[Strategy] = []
            def clone(self) -> "Strategy": ...
    """)
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert "class NewStrategy:" in out
    assert "registry: list[NewStrategy] = []" in out
    # The forward reference ``"Strategy"`` is a string literal — preserved.
    assert 'def clone(self) -> "Strategy": ...' in out


def test_rename_identifier_no_op_when_symbol_absent():
    """Source without any matching NAME token comes back unchanged."""
    src = "class OtherThing:\n    pass\n"
    out = _rename_identifier_in_source(src, original="Strategy", new="NewStrategy")
    assert out == src


# ---------------------------------------------------------------------------
# seed_sibling — end-to-end
# ---------------------------------------------------------------------------


def _sibling_spec(tmp_path: Path, target: str = "src/strategy.py") -> ProblemSpec:
    """Build a minimal sibling-mode ``ProblemSpec`` rooted at *tmp_path*."""
    return ProblemSpec(
        description="x",
        mode="algorithm",
        eval_command="python bench.py --strategy {symbol}",
        metrics=[Metric(name="m", optimise=OptimiseDirection.MINIMIZE)],
        scope=ScopeSpec(target_files=[target]),
        evolution=EvolutionSpec(),
        runtime_mode=RuntimeModeSpec(),
        safety=SafetySpec(),
        backend=BackendSpec(type="local"),
        artifact_mode="sibling",
        sibling=SiblingSpec(),
    )


def test_seed_sibling_writes_renamed_file_alongside_original(tmp_path):
    """End-to-end: target file -> seeded sibling with renamed symbol + new path."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    target = src_dir / "strategy.py"
    target.write_text(
        "class Strategy:\n"
        "    name = 'Strategy'\n"
        "    def run(self): return Strategy.name\n",
        encoding="utf-8",
    )

    spec = _sibling_spec(tmp_path)
    result = seed_sibling(
        spec, problem_id="multiasset", today=date(2026, 4, 30), repo_root=tmp_path,
    )

    assert isinstance(result, SeedResult)
    assert result.original_symbol == "Strategy"
    assert result.new_symbol == "StrategyMultiasset20260430"

    new_path = Path(result.new_path)
    assert new_path.exists()
    assert new_path.parent == src_dir
    assert new_path.name == "strategy_multiasset_2026_04_30.py"

    new_source = new_path.read_text(encoding="utf-8")
    assert "class StrategyMultiasset20260430:" in new_source
    # Symbol references rewritten...
    assert "return StrategyMultiasset20260430.name" in new_source
    # ...but the string literal containing the original name is preserved.
    assert "name = 'Strategy'" in new_source

    # Original file untouched.
    assert target.read_text(encoding="utf-8").startswith("class Strategy:")


def test_seed_sibling_honours_custom_output_dir(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "strategy.py").write_text("class Strategy: pass\n", encoding="utf-8")

    spec = _sibling_spec(tmp_path)
    spec = _replace(spec, sibling=SiblingSpec(output_dir="generated/strategies"))

    result = seed_sibling(
        spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
    )
    new_path = Path(result.new_path)
    assert new_path.parent == tmp_path / "generated" / "strategies"
    assert new_path.exists()


def test_seed_sibling_refuses_to_overwrite_existing_file(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "strategy.py").write_text("class Strategy: pass\n", encoding="utf-8")

    spec = _sibling_spec(tmp_path)
    seed_sibling(
        spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
    )
    # Second invocation with same problem_id + date hits the same destination.
    with pytest.raises(SiblingSeedError, match="already exists"):
        seed_sibling(
            spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
        )


def test_seed_sibling_errors_on_non_python_target(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "config.toml").write_text("[strategy]\nname = 'Strategy'\n", encoding="utf-8")

    spec = _sibling_spec(tmp_path, target="src/config.toml")
    with pytest.raises(SiblingSeedError, match="Python-only"):
        seed_sibling(
            spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
        )


def test_seed_sibling_errors_on_missing_target(tmp_path):
    spec = _sibling_spec(tmp_path)
    with pytest.raises(SiblingSeedError, match="does not exist"):
        seed_sibling(
            spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
        )


def test_seed_sibling_errors_on_wrong_artifact_mode(tmp_path):
    """Calling with ``artifact_mode='replace'`` is a programming error."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "strategy.py").write_text("class Strategy: pass\n", encoding="utf-8")

    spec = _sibling_spec(tmp_path)
    spec = _replace(spec, artifact_mode="replace", sibling=None)

    with pytest.raises(SiblingSeedError, match="expected 'sibling'"):
        seed_sibling(
            spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
        )


def test_seed_sibling_uses_explicit_symbol_name(tmp_path):
    """``sibling.symbol_name`` overrides the auto-detection heuristic."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "strategy.py").write_text(
        "class A: pass\nclass B: pass\n", encoding="utf-8",
    )

    spec = _sibling_spec(tmp_path)
    spec = _replace(spec, sibling=SiblingSpec(symbol_name="B"))
    result = seed_sibling(
        spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
    )
    assert result.original_symbol == "B"
    assert result.new_symbol == "BX20260430"
    assert "class BX20260430: pass" in Path(result.new_path).read_text()


def test_seed_sibling_errors_when_pattern_produces_invalid_identifier(tmp_path):
    """A symbol_rename_pattern with non-identifier characters is caught at seed time."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "strategy.py").write_text("class Strategy: pass\n", encoding="utf-8")

    spec = _sibling_spec(tmp_path)
    spec = _replace(
        spec,
        sibling=SiblingSpec(symbol_rename_pattern="{original}-{problem_id}"),
    )
    with pytest.raises(SiblingSeedError, match="invalid Python identifier"):
        seed_sibling(
            spec, problem_id="x", today=date(2026, 4, 30), repo_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# ProblemSpec.eval_command_for
# ---------------------------------------------------------------------------


def test_eval_command_for_substitutes_symbol_token():
    spec = _sibling_spec(Path("/tmp"))
    cmd = spec.eval_command_for("MyNewSymbol")
    assert cmd == "python bench.py --strategy MyNewSymbol"


def test_eval_command_for_returns_command_unchanged_without_token():
    spec = ProblemSpec(
        description="x",
        mode="algorithm",
        eval_command="python bench.py",
        metrics=[Metric(name="m", optimise=OptimiseDirection.MINIMIZE)],
        scope=ScopeSpec(target_files=["a.py"]),
        evolution=EvolutionSpec(),
        runtime_mode=RuntimeModeSpec(),
        safety=SafetySpec(),
        backend=BackendSpec(type="local"),
    )
    assert spec.eval_command_for("Foo") == "python bench.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _replace(spec: ProblemSpec, **kwargs):
    """Frozen-dataclass-aware replacement helper for tests."""
    from dataclasses import replace
    return replace(spec, **kwargs)
