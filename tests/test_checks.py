"""Machine diagnostics.

`run_checks` turns a `Fingerprint` snapshot into actionable warnings, each
skipping itself when its field is `None`.
"""

from bench.core.diagnostic import run_checks
from bench.core.fingerprint import Fingerprint


def _clean() -> Fingerprint:
    return Fingerprint(
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


def test_all_none_fields_skipped():
    # A default snapshot (everything unknown) yields no diagnostics.
    assert run_checks(Fingerprint()) == []


def test_governor_not_performance_is_high():
    diags = run_checks(Fingerprint(governors=["powersave"]))
    assert len(diags) == 1
    assert diags[0].severity == "high"
    assert "cpupower" in (diags[0].fix or "")


def test_turbo_enabled_warns():
    diags = run_checks(Fingerprint(turbo_enabled=True))
    assert [d.severity for d in diags] == ["warn"]


def test_aslr_enabled_warns_with_setarch_fix():
    diags = run_checks(Fingerprint(aslr=2))
    assert len(diags) == 1
    assert "setarch" in (diags[0].fix or "")


def test_smt_enabled_warns():
    assert [d.severity for d in run_checks(Fingerprint(smt_enabled=True))] == ["warn"]


def test_thp_enabled_warns():
    diags = run_checks(Fingerprint(transparent_hugepage="always"))
    assert [d.severity for d in diags] == ["warn"]
    assert "transparent_hugepage" in (diags[0].fix or "")


def test_swap_in_use_warns():
    assert [d.severity for d in run_checks(Fingerprint(swap_in_use=True))] == ["warn"]


def test_on_battery_is_high():
    assert [d.severity for d in run_checks(Fingerprint(on_battery=True))] == ["high"]


def test_low_power_mode_is_high_with_pmset_fix():
    diags = run_checks(Fingerprint(low_power_mode=True))
    assert diags[0].severity == "high"
    assert "pmset" in (diags[0].fix or "")


def test_high_load_warns():
    diags = run_checks(Fingerprint(load_avg=[7.0, 6.0, 5.0], logical_cpus=8))
    assert [d.severity for d in diags] == ["warn"]
