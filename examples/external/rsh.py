#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "../..", editable = true }
# ///
"""RBenchmarking (rsh) configuration — port of reactorlabs/RBenchmarking/rebench.conf.

Demonstrates:
- Five suites sharing a single benchmark invocation pattern.
- Per-benchmark ``cmd`` overrides (shootout uses ``subfolder/name``).
- Minimal R harness: an inline R profile dumped to a tempfile, wired via
  ``R_PROFILE_USER``. The benchmark file itself is the script ``Rscript`` runs.
  ``.Last`` runs the timing loop after the benchmark file finishes sourcing.

The rebench config defines three executors (GNU-R, PIR-LLVM, FASTR) that differ
only in the ``Rscript`` path. Here, the executor is selected at the CLI via
``--Rscript /path/to/Rscript``.
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchr import bench as B, Context, FloatPerLine, max_rss, run, suite


_HARNESS_R = r"""
# Auto-sourced via R_PROFILE_USER before the benchmark file runs.
# Benchmark file is expected to define execute(param) and (optionally) verifyResult(result, param).
# After the benchmark file finishes, .Last runs the timing loop.

if (!exists("verifyResult"))
    verifyResult <- function(result, param) TRUE

.Last <- function() {
    args <- commandArgs(trailingOnly = TRUE)
    iterations <- strtoi(args[[1]])
    param <- strtoi(args[[2]])
    for (i in 1:iterations) {
        t0 <- Sys.time()
        result <- execute(param)
        if (!verifyResult(result, param))
            stop("Benchmark failed")
        cat((as.numeric(Sys.time()) - as.numeric(t0)) * 1e6, "\n")
    }
}
"""


@dataclass
class RshParams:
    Rscript: Path                          # required: Rscript executable (GNU-R, PIR-LLVM, FASTR, ...)
    benchmarks_path: Path                  # required: path to RBenchmarking/Benchmarks
    iterations: int = 15                   # rebench: iterations (harness inner loop)


def _cmd(ctx: Context[RshParams]):
    return [
        str(ctx.params.Rscript),
        f"{ctx.matrix.cmd}.r",
        str(ctx.params.iterations),
        str(ctx.matrix.extra_args),
    ]


def _suite_cwd(subdir: str):
    return lambda ctx: ctx.params.benchmarks_path / subdir


# ----------------------------------------------------------------------
# Suites
# ----------------------------------------------------------------------

_PROC = (FloatPerLine("us").lower_is_better(), max_rss())


are_we_fast_r = (
    suite("are-we-fast-r")
    .add(B("Mandelbrot",            cmd="Mandelbrot",            extra_args=500))
    .add(B("Bounce",                cmd="Bounce",                extra_args=35))
    .add(B("Bounce_nonames",        cmd="Bounce_nonames",        extra_args=35))
    .add(B("Bounce_nonames_simple", cmd="Bounce_nonames_simple", extra_args=35))
    .add(B("Storage",               cmd="Storage",               extra_args=100))
    .with_cwd(_suite_cwd("areWeFast"))
    .with_command(_cmd)
    .with_timeout(6000)
    .with_metric(*_PROC)
)


shootout = (
    suite("shootout")
    .add(B("binarytrees",             cmd="binarytrees/binarytrees",                   extra_args=9))
    .add(B("binarytrees_2",           cmd="binarytrees/binarytrees_2",                 extra_args=9))
    .add(B("binarytrees_naive",       cmd="binarytrees/binarytrees_naive",             extra_args=9))
    .add(B("fannkuchredux",           cmd="fannkuch/fannkuchredux",                    extra_args=9))
    .add(B("fannkuchredux_naive",     cmd="fannkuch/fannkuchredux_naive",              extra_args=9))
    .add(B("fasta",                   cmd="fasta/fasta",                               extra_args=60000))
    .add(B("fasta_2",                 cmd="fasta/fasta_2",                             extra_args=60000))
    .add(B("fasta_3",                 cmd="fasta/fasta_3",                             extra_args=60000))
    .add(B("fasta_naive",             cmd="fasta/fasta_naive",                         extra_args=80000))
    .add(B("fasta_naive_2",           cmd="fasta/fasta_naive_2",                       extra_args=80000))
    .add(B("fastaredux",              cmd="fastaredux/fastaredux",                     extra_args=80000))
    .add(B("fastaredux_naive",        cmd="fastaredux/fastaredux_naive",               extra_args=80000))
    .add(B("knucleotide",             cmd="knucleotide/knucleotide",                   extra_args=2000))
    .add(B("knucleotide_brute",       cmd="knucleotide/knucleotide_brute",             extra_args=2000))
    .add(B("knucleotide_brute_2",     cmd="knucleotide/knucleotide_brute_2",           extra_args=2000))
    .add(B("knucleotide_brute_3",     cmd="knucleotide/knucleotide_brute_3",           extra_args=2000))
    .add(B("mandelbrot_ascii",        cmd="mandelbrot/mandelbrot_ascii",               extra_args=300))
    .add(B("mandelbrot_naive_ascii",  cmd="mandelbrot/mandelbrot_naive_ascii",         extra_args=200))
    .add(B("mandelbrot_noout",        cmd="mandelbrot/mandelbrot_noout",               extra_args=400))
    .add(B("mandelbrot_noout_naive",  cmd="mandelbrot/mandelbrot_noout_naive",         extra_args=500))
    .add(B("nbody",                   cmd="nbody/nbody",                               extra_args=25000))
    .add(B("nbody_2",                 cmd="nbody/nbody_2",                             extra_args=12000))
    .add(B("nbody_3",                 cmd="nbody/nbody_3",                             extra_args=20000))
    .add(B("nbody_naive",             cmd="nbody/nbody_naive",                         extra_args=20000))
    .add(B("nbody_naive_2",           cmd="nbody/nbody_naive_2",                       extra_args=20000))
    .add(B("pidigits",                cmd="pidigits/pidigits",                         extra_args=30))
    .add(B("regexdna",                cmd="regexdna/regexdna",                         extra_args=500000))
    .add(B("reversecomplement",       cmd="reversecomplement/reversecomplement",       extra_args=150000))
    .add(B("reversecomplement_2",     cmd="reversecomplement/reversecomplement_2",     extra_args=150000))
    .add(B("reversecomplement_naive", cmd="reversecomplement/reversecomplement_naive", extra_args=50000))
    .add(B("spectralnorm",            cmd="spectralnorm/spectralnorm",                 extra_args=1200))
    .add(B("spectralnorm_alt",        cmd="spectralnorm/spectralnorm_alt",             extra_args=1500))
    .add(B("spectralnorm_alt_2",      cmd="spectralnorm/spectralnorm_alt_2",           extra_args=1200))
    .add(B("spectralnorm_alt_3",      cmd="spectralnorm/spectralnorm_alt_3",           extra_args=250))
    .add(B("spectralnorm_math",       cmd="spectralnorm/spectralnorm_math",            extra_args=1200))
    .add(B("spectralnorm_naive",      cmd="spectralnorm/spectralnorm_naive",           extra_args=150))
    .with_cwd(_suite_cwd("shootout"))
    .with_command(_cmd)
    .with_timeout(6000)
    .with_metric(*_PROC)
)


simple_extra = (
    suite("simple_extra")
    .add(B("listFor",   cmd="list-for",   extra_args=500000))
    .add(B("listWhile", cmd="list-while", extra_args=500000))
    .with_cwd(_suite_cwd("simple"))
    .with_command(_cmd)
    .with_timeout(6000)
    .with_metric(*_PROC)
)


simple_reduced = (
    suite("simple_reduced")
    .add(B("bytecodes",               cmd="bytecodes",                extra_args=9000000))
    .add(B("emptyFor",                cmd="empty-for",                extra_args=100000000))
    .add(B("emptyWhile",              cmd="empty-while",              extra_args=30000000))
    .add(B("lapply",                  cmd="lapply",                   extra_args=1000000))
    .add(B("lapplyDots",              cmd="lapply-dots",              extra_args=1000000))
    .add(B("matrixFor",               cmd="matrix-for",               extra_args=2000))
    .add(B("profiler-microbenchmark", cmd="profiler-microbenchmark",  extra_args=5000000))
    .add(B("profiler-rsa",            cmd="profiler-rsa",             extra_args=600000))
    .add(B("profiler-shared",         cmd="profiler-shared",          extra_args=20))
    .add(B("scalarFor",               cmd="scalar-for",               extra_args=100000000))
    .add(B("superWhile",              cmd="super-while",              extra_args=20000000))
    .add(B("vectorFor",               cmd="vector-for",               extra_args=15000000))
    .add(B("vectorWhile",             cmd="vector-while",             extra_args=20000000))
    .add(B("scalarWhile",             cmd="scalar-while",             extra_args=25000000))
    .with_cwd(_suite_cwd("simple"))
    .with_command(_cmd)
    .with_timeout(6000)
    .with_metric(*_PROC)
)


real_thing = (
    suite("real_thing")
    .add(B("convolution",      cmd="convolution",      extra_args=500))
    .add(B("convolution_v",    cmd="convolution_v",    extra_args=1000))
    .add(B("convolution_slow", cmd="convolution_slow", extra_args=1500))
    .add(B("volcano",          cmd="volcano",          extra_args=1))
    .add(B("flexclust",        cmd="flexclust",        extra_args=5))
    .add(B("flexclust_no_s4",  cmd="flexclust_no_s4",  extra_args=5))
    .with_cwd(_suite_cwd("RealThing"))
    .with_command(_cmd)
    .with_timeout(6000)
    .with_metric(*_PROC)
)


SUITES = [are_we_fast_r, shootout, simple_extra, simple_reduced, real_thing]


if __name__ == "__main__":
    fd, harness_path = tempfile.mkstemp(prefix="rsh_harness_", suffix=".R")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_HARNESS_R)
        env = {"R_PROFILE_USER": harness_path}
        suites = [s.with_env(env) for s in SUITES]
        run(suites, params=RshParams)
    finally:
        os.unlink(harness_path)
