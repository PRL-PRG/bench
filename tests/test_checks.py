"""Machine diagnostics.

`run_checks` turns a `Fingerprint` snapshot into actionable warnings, each
skipping itself when the probe did not record its fact. The snapshot has to be
a `SystemEnvironment` - a fingerprint without the platform-independent base
facts is not a machine description and is checked by nothing.
"""

from typing import Any

from bench.core.diagnostic import run_checks
from bench.core.fingerprint import Fingerprint

# The platform-independent facts `SystemProbe` always merges in. `run_checks`
# gates on them, so every snapshot under test carries them.
_BASE: dict[str, Any] = {
    "timestamp": "2026-01-01T00:00:00+00:00",
    "hostname": "testhost",
    "system": "Linux",
    "release": "6.0.0",
    "machine": "x86_64",
    "python_version": "3.14.0",
}

# Base facts the probe records only when the platform can answer.
_BASE_OPTIONAL: dict[str, Any] = {"load_avg": [0.0, 0.0, 0.0], "logical_cpus": 8}


def _fp(**facts: Any) -> Fingerprint:
    return Fingerprint({**_BASE, **_BASE_OPTIONAL, **facts})


def _clean() -> Fingerprint:
    return _fp(
        governors=["performance"],
        turbo_enabled=False,
        aslr=0,
        transparent_hugepage="never",
        smt_enabled=False,
        swap_in_use=False,
        on_battery=False,
        low_power_mode=False,
        load_avg=[0.1, 0.1, 0.1],
        logical_cpus=8,
    )


def test_no_diagnostics_on_clean_machine():
    assert run_checks(_clean()) == []


def test_unrecorded_facts_are_skipped():
    # The base facts alone (nothing else observed) yield no diagnostics.
    assert run_checks(_fp()) == []


def test_a_snapshot_that_is_not_a_system_environment_is_not_checked():
    # No base facts: this is not a machine description, so no check claims it.
    assert run_checks(Fingerprint({"governors": ["powersave"]})) == []
    assert run_checks(Fingerprint({})) == []


def test_optional_base_facts_are_not_required_to_be_checked():
    # `load_avg` and `logical_cpus` are `NotRequired`, so a machine that cannot
    # report them is still a system environment.
    snapshot = Fingerprint({**_BASE, "governors": ["powersave"]})
    assert [d.severity for d in run_checks(snapshot)] == ["high"]


def test_governor_not_performance_is_high():
    diags = run_checks(_fp(governors=["powersave"]))
    assert len(diags) == 1
    assert diags[0].severity == "high"
    assert "cpupower" in (diags[0].fix or "")


def test_turbo_enabled_warns():
    diags = run_checks(_fp(turbo_enabled=True))
    assert [d.severity for d in diags] == ["warn"]


def test_aslr_enabled_warns_with_setarch_fix():
    diags = run_checks(_fp(aslr=2))
    assert len(diags) == 1
    assert "setarch" in (diags[0].fix or "")


def test_smt_enabled_warns():
    # A Unix-wide fact: checked once, not once per platform.
    assert [d.severity for d in run_checks(_fp(smt_enabled=True))] == ["warn"]


def test_thp_enabled_warns():
    diags = run_checks(_fp(transparent_hugepage="always"))
    assert [d.severity for d in diags] == ["warn"]
    assert "transparent_hugepage" in (diags[0].fix or "")


def test_swap_in_use_warns():
    # A Unix-wide fact: checked once, not once per platform.
    assert [d.severity for d in run_checks(_fp(swap_in_use=True))] == ["warn"]


def test_on_battery_is_high():
    # A Unix-wide fact: checked once, not once per platform.
    assert [d.severity for d in run_checks(_fp(on_battery=True))] == ["high"]


def test_low_power_mode_is_high_with_pmset_fix():
    diags = run_checks(_fp(low_power_mode=True))
    assert diags[0].severity == "high"
    assert "pmset" in (diags[0].fix or "")


def test_high_load_warns():
    diags = run_checks(_fp(load_avg=[7.0, 6.0, 5.0], logical_cpus=8))
    assert [d.severity for d in diags] == ["warn"]
