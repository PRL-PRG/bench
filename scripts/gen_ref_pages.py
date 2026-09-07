"""Generate one API reference page per module, plus the nav that lists them."""

from pathlib import Path

import mkdocs_gen_files

SRC = Path(__file__).parent.parent / "src"

nav = mkdocs_gen_files.Nav()


def identifier(parts: tuple[str, ...]) -> str:
    """Dotted name mkdocstrings can actually collect for a module."""
    for depth in range(1, len(parts)):
        if not (SRC.joinpath(*parts[:depth]) / "__init__.py").exists():
            return ".".join(parts[depth - 1 :])
    return ".".join(parts)


for path in sorted(SRC.rglob("*.py")):
    module_path = path.relative_to(SRC).with_suffix("")
    doc_path = path.relative_to(SRC).with_suffix(".md")
    parts = tuple(module_path.parts)

    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
    elif parts[-1].startswith("_"):
        continue

    nav[parts] = doc_path.as_posix()

    full_name = ".".join(parts)
    collected_name = identifier(parts)

    with mkdocs_gen_files.open(Path("api", doc_path), "w") as fd:
        fd.write(f"::: {collected_name}\n")
        if collected_name != full_name:
            fd.write("    options:\n")
            fd.write(f"      heading: {full_name}\n")

    mkdocs_gen_files.set_edit_path(Path("api", doc_path), path)

with mkdocs_gen_files.open("api/SUMMARY.md", "w") as fd:
    fd.writelines(nav.build_literate_nav())
