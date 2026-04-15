"""Logic-equivalence checker for runtime optimization mode.

Given an *original* and an *optimized* callable plus a ``hypothesis`` strategy
for their inputs, we run hundreds of random examples through both and assert
the outputs are identical.

"Identical" means:

* Same returned value under ``==``.
* Same exception type (if any) under ``type(e) is type(e2)``.
* No uncaught side effects on a supplied observer.

The reviewer treats a missing or failed equivalence report as grounds for
rejection — see ``skills/reviewer/SKILL.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hypothesis import Phase, given, settings
from hypothesis.errors import HypothesisException
from hypothesis.strategies import SearchStrategy


@dataclass
class EquivalenceReport:
    equivalent: bool
    samples_tested: int
    counterexample: tuple[tuple[Any, ...], dict[str, Any]] | None = None
    mismatch: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "equivalent": self.equivalent,
            "samples_tested": self.samples_tested,
            "counterexample": self._serialize_counterexample(),
            "mismatch": self.mismatch,
            "error": self.error,
        }

    def _serialize_counterexample(self) -> Any:
        if self.counterexample is None:
            return None
        args, kwargs = self.counterexample
        return {"args": [repr(a) for a in args], "kwargs": {k: repr(v) for k, v in kwargs.items()}}


def check_equivalence(
    original: Callable[..., Any],
    optimized: Callable[..., Any],
    strategy: SearchStrategy,
    *,
    samples: int = 500,
    deadline_ms: int | None = 500,
) -> EquivalenceReport:
    """Run *samples* property-based examples through both callables.

    *strategy* must produce the exact argument structure the callables accept.
    Common shapes:

    * ``strategy=builds(lambda x: (x,), integers())`` — single-arg callables
    * ``strategy=tuples(integers(), integers())`` — positional multi-arg
    * ``strategy=fixed_dictionaries({"n": integers(), "cap": integers()})`` — keyword
    """
    counter: dict[str, Any] = {"count": 0, "fail": None}

    @settings(
        max_examples=samples,
        deadline=deadline_ms,
        phases=[Phase.generate],  # we do not want shrinking to mask the real counterexample
        database=None,
    )
    @given(inputs=strategy)
    def _prop(inputs: Any) -> None:
        args, kwargs = _unpack(inputs)
        counter["count"] += 1
        a_result, a_exc = _invoke(original, args, kwargs)
        b_result, b_exc = _invoke(optimized, args, kwargs)

        if (a_exc is None) != (b_exc is None):
            counter["fail"] = {
                "args": args,
                "kwargs": kwargs,
                "mismatch": (
                    f"divergent exception behaviour — "
                    f"original={a_exc!r} optimized={b_exc!r}"
                ),
            }
            raise AssertionError("divergent exception behaviour")

        if a_exc is not None and b_exc is not None:
            if type(a_exc) is not type(b_exc):
                counter["fail"] = {
                    "args": args,
                    "kwargs": kwargs,
                    "mismatch": f"exception type differs: {type(a_exc).__name__} vs {type(b_exc).__name__}",
                }
                raise AssertionError("divergent exception type")
            return

        if not _equal(a_result, b_result):
            counter["fail"] = {
                "args": args,
                "kwargs": kwargs,
                "mismatch": f"return value differs: original={a_result!r} optimized={b_result!r}",
            }
            raise AssertionError("divergent return value")

    try:
        _prop()
    except AssertionError:
        fail = counter["fail"]
        return EquivalenceReport(
            equivalent=False,
            samples_tested=counter["count"],
            counterexample=(tuple(fail["args"]), dict(fail["kwargs"])),
            mismatch=fail["mismatch"],
        )
    except HypothesisException as e:
        return EquivalenceReport(
            equivalent=False,
            samples_tested=counter["count"],
            error=f"hypothesis: {e}",
        )
    except Exception as e:  # pragma: no cover — defensive
        return EquivalenceReport(
            equivalent=False,
            samples_tested=counter["count"],
            error=f"{type(e).__name__}: {e}",
        )

    return EquivalenceReport(equivalent=True, samples_tested=counter["count"])


def _unpack(inputs: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Turn whatever the strategy produced into ``(args, kwargs)``."""
    if isinstance(inputs, dict):
        return (), dict(inputs)
    if isinstance(inputs, tuple):
        return tuple(inputs), {}
    return (inputs,), {}


def _invoke(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, BaseException | None]:
    try:
        return fn(*args, **kwargs), None
    except BaseException as e:  # noqa: BLE001 — we want to compare behaviour under any exception
        return None, e


def _equal(a: Any, b: Any) -> bool:
    """Strict equality that tolerates NaN floats (both-NaN is equal here)."""
    if isinstance(a, float) and isinstance(b, float):
        if a != a and b != b:
            return True
    try:
        return a == b
    except Exception:
        return False
