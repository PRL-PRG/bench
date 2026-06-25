"""Outlier detection strategies.

`ModifiedZScore` ports hyperfine's modified Z-score / MAD test, so the cases
below mirror hyperfine's own `outlier_detection.rs` tests.
"""

from bench.core.outlier import ModifiedZScore, NoDetection, modified_zscores


def _num_outliers(xs: list[float]) -> int:
    return sum(ModifiedZScore().detect(xs))


def test_no_outliers_in_small_samples():
    assert _num_outliers([]) == 0
    assert _num_outliers([50.0]) == 0
    assert _num_outliers([1000.0, 0.0]) == 0


def test_no_outliers_in_low_variance_sample():
    assert _num_outliers([-0.2, 0.0, 0.2]) == 0


def test_detects_single_outlier():
    assert _num_outliers([-0.2, 0.0, 0.2, 4.0]) == 1
    assert _num_outliers([0.5, 0.30, 0.29, 0.31, 0.30]) == 1


def test_no_outliers_in_normal_sample():
    xs = [
        2.33269488,
        1.42195907,
        -0.57527698,
        -0.31293437,
        2.2948158,
        0.75813273,
        -1.0712388,
        -0.96394741,
        -1.15897446,
        1.10976285,
    ]
    assert _num_outliers(xs) == 0


def test_detects_two_manually_added_outliers():
    xs = [
        2.33269488,
        1.42195907,
        -0.57527698,
        -0.31293437,
        2.2948158,
        0.75813273,
        -1.0712388,
        -0.96394741,
        -1.15897446,
        1.10976285,
        20.0,
        -500.0,
    ]
    assert _num_outliers(xs) == 2


def test_no_outliers_when_mad_is_zero():
    # Exact-fit (MAD==0, >50% tied) means the metric has no robust spread, so we
    # report no outliers. hyperfine only detects on wall-clock time, which always
    # varies, so it never hits this case (its epsilon guard is dead code).
    # See https://stats.stackexchange.com/q/339932
    assert _num_outliers([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 100.0]) == 0
    assert _num_outliers([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 100.0, 100.0]) == 0


def test_modified_zscores_zero_when_no_spread():
    # MAD==0: no robust spread -> every score is 0 (no outliers), rather than an
    # epsilon/inf-driven "severe outlier" for any differing value.
    assert modified_zscores([10.0, 10.0, 10.0, 100.0]) == [0.0, 0.0, 0.0, 0.0]
    assert modified_zscores([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_no_outliers_for_near_constant_metric():
    # Real case: heap_after = 76.252 (×48) + one 76.159. A 0.12% jitter must not
    # be reported as a statistical outlier just because the rest are bit-identical.
    assert _num_outliers([76.252] * 48 + [76.159]) == 0


def test_detect_returns_mask_aligned_with_input():
    mask = ModifiedZScore().detect([-0.2, 0.0, 0.2, 4.0])
    assert mask == [False, False, False, True]


def test_no_detection_flags_nothing():
    assert NoDetection().detect([1.0, 1000.0, -500.0]) == [False, False, False]
    assert NoDetection().detect([]) == []
