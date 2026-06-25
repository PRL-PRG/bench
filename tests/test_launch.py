"""Launch helpers: argv generators the user prepends to their own command."""

from bench.launch import nice, setarch_no_aslr, taskset


def test_taskset_int():
    assert taskset(2) == ["taskset", "-c", "2"]


def test_taskset_iterable():
    assert taskset([0, 1, 3]) == ["taskset", "-c", "0,1,3"]


def test_taskset_string_spec():
    assert taskset("0-3") == ["taskset", "-c", "0-3"]


def test_nice():
    assert nice(-5) == ["nice", "-n", "-5"]


def test_setarch_no_aslr():
    assert setarch_no_aslr() == ["setarch", "-R"]


def test_compose_into_command():
    # The intended use: prepend to your own command (recorded as-is).
    cmd = [*taskset(0), *nice(-20), "sleep", "0.1"]
    assert cmd == ["taskset", "-c", "0", "nice", "-n", "-20", "sleep", "0.1"]
