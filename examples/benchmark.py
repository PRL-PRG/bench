#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Multi-suite R benchmark configuration.

Demonstrates:
- Five suites sharing a common locale environment.
- Mixing static and dynamic working directories.
- ``Rebench`` processor over many benchmark families.
"""

from dataclasses import dataclass

from benchr import B, P, Path, run, suite


HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
BENCHMARKS = INPUTS / "Benchmarks"


LOCALE = {
    "LC_CTYPE": "en_US.UTF-8",
    "LC_TIME": "en_US.UTF-8",
    "LC_MONETARY": "en_US.UTF-8",
    "LC_PAPER": "en_US.UTF-8",
    "LC_ADDRESS": "C",
    "LC_MEASUREMENT": "en_US.UTF-8",
    "LC_NUMERIC": "C",
    "LC_COLLATE": "en_US.UTF-8",
    "LC_MESSAGES": "en_US.UTF-8",
    "LC_NAME": "C",
    "LC_TELEPHONE": "C",
    "LC_IDENTIFICATION": "C",
}


@dataclass
class RParams:
    Rpath: Path                        # required: path to R install
    iterations: int = 15               # optional


def _rscript(ctx: RParams) -> str:
    return str(ctx.Rpath / "bin" / "Rscript")


# ----------------------------------------------------------------------
# Suites
# ----------------------------------------------------------------------

areWeFast = (
    suite("areWeFast")
    .add(B("Mandelbrot", size=500))
    .add(B("Bounce", size=35))
    .add(B("Bounce_nonames", size=35))
    .add(B("Bounce_nonames_simple", size=35))
    .add(B("Storage", size=100))
    .with_cwd(BENCHMARKS / "areWeFast")
    .with_command(lambda b, ctx: [_rscript(ctx), "harness.r", b.name, str(ctx.iterations), str(b.size)])
    .with_process(P.rebench())
)


# Shootout: each benchmark has its own subfolder, derived from a data attr.
def _shootout(name: str, subfolder: str, arg: int):
    return B(name, subfolder=subfolder, arg=arg)


shootout = (
    suite("shootout")
    .add(_shootout("binarytrees",            "binarytrees",      9))
    .add(_shootout("fannkuchredux",          "fannkuch",         9))
    .add(_shootout("fasta",                  "fasta",            60000))
    .add(_shootout("fastaredux",             "fastaredux",       80000))
    .add(_shootout("knucleotide",            "knucleotide",      2000))
    .add(_shootout("mandelbrot_ascii",       "mandelbrot",       300))
    .add(_shootout("mandelbrot_naive_ascii", "mandelbrot",       200))
    .add(_shootout("nbody",                  "nbody",            25000))
    .add(_shootout("nbody_naive",            "nbody",            20000))
    .add(_shootout("pidigits",               "pidigits",         30))
    .add(_shootout("regexdna",               "regexdna",         500000))
    .add(_shootout("reversecomplement",      "reversecomplement", 150000))
    .add(_shootout("spectralnorm",           "spectralnorm",     1200))
    .add(_shootout("spectralnorm_math",      "spectralnorm",     1200))
    .with_cwd(lambda b, ctx: BENCHMARKS / "shootout" / b.subfolder)
    .with_command(lambda b, ctx: [_rscript(ctx), "harness.r", b.name, str(ctx.iterations), str(b.arg)])
    .with_process(P.rebench())
)


realThing = (
    suite("RealThing")
    .add(B("convolution", size=500))
    .add(B("convolution_slow", size=1500))
    .add(B("volcano", size=1))
    .add(B("flexclust", size=5))
    .with_cwd(BENCHMARKS / "RealThing")
    .with_command(lambda b, ctx: [_rscript(ctx), "harness.r", b.name, str(ctx.iterations), str(b.size)])
    .with_process(P.rebench())
)


kaggle = (
    suite("kaggle")
    .add(B("basic-analysis"))
    .add(B("bolt-driver"))
    .add(B("london-airbnb"))
    .add(B("placement"))
    .add(B("titanic"))
    .with_cwd(lambda b, ctx: INPUTS / "kaggle" / b.name)
    .with_command(lambda b, ctx: [_rscript(ctx), "../../harness.r", b.name, str(ctx.iterations)])
    .with_process(P.rebench())
)


recommenderlab = (
    suite("recommenderlab")
    .add(B("recommenderlab"))
    .with_cwd(INPUTS / "recommenderlab")
    .with_command(lambda b, ctx: [_rscript(ctx), "runner.r"])
    .with_process(P.rebench())
)


SUITES = [areWeFast, shootout, realThing, kaggle, recommenderlab]
# Apply the locale env to every benchmark.
SUITES = [s.with_env(LOCALE) for s in SUITES]


if __name__ == "__main__":
    run(SUITES, params=RParams)
