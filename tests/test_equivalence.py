"""Equivalence checker tests."""

from __future__ import annotations

from hypothesis.strategies import integers, tuples, lists

from agent_evolve.eval import check_equivalence


def test_equivalent_pair_passes():
    def slow_sum(a: int, b: int) -> int:
        return sum(range(a, a + b)) if b > 0 else 0

    def fast_sum(a: int, b: int) -> int:
        if b <= 0:
            return 0
        return b * (2 * a + b - 1) // 2

    report = check_equivalence(
        slow_sum, fast_sum,
        tuples(integers(min_value=0, max_value=100), integers(min_value=0, max_value=100)),
        samples=50,
    )
    assert report.equivalent
    assert report.samples_tested == 50


def test_broken_pair_fails():
    def original(a: int, b: int) -> int:
        return sum(range(a, a + b)) if b > 0 else 0

    def broken(a: int, b: int) -> int:
        if b <= 0:
            return 0
        return b * (2 * a + b) // 2  # off by one

    report = check_equivalence(
        original, broken,
        tuples(integers(min_value=0, max_value=50), integers(min_value=1, max_value=50)),
        samples=50,
    )
    assert not report.equivalent
    assert report.counterexample is not None


def test_divergent_exception_behaviour():
    def original(x: int) -> int:
        return 10 // x

    def broken(x: int) -> int:
        if x == 0:
            return 0  # silently returns instead of raising
        return 10 // x

    report = check_equivalence(original, broken, tuples(integers(min_value=0, max_value=5)), samples=30)
    assert not report.equivalent
    assert report.mismatch and "exception" in report.mismatch.lower()


def test_list_return_equivalence():
    def slow_reverse(xs: list[int]) -> list[int]:
        out = []
        for x in xs:
            out.insert(0, x)
        return out

    def fast_reverse(xs: list[int]) -> list[int]:
        return xs[::-1]

    report = check_equivalence(
        slow_reverse, fast_reverse,
        tuples(lists(integers(), max_size=20)),
        samples=30,
    )
    assert report.equivalent


def test_nan_floats_tolerated():
    def a(x: float) -> float:
        return float("nan") if x == 0 else 1.0 / x

    def b(x: float) -> float:
        return float("nan") if x == 0 else 1.0 / x

    from hypothesis.strategies import floats
    report = check_equivalence(
        a, b, tuples(floats(min_value=-1.0, max_value=1.0, allow_nan=False)),
        samples=20,
    )
    assert report.equivalent


def test_report_to_dict_serializable():
    def f(x: int) -> int:
        return x + 1

    report = check_equivalence(f, f, tuples(integers()), samples=10)
    d = report.to_dict()
    assert d["equivalent"] is True
    assert d["samples_tested"] == 10
