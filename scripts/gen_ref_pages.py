"""Generate one API reference page per module, plus the nav that lists them."""

from pathlib import Path

import mkdocs_gen_files

SRC = Path(__file__).parent.parent / "src"

nav = mkdocs_gen_files.Nav()

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

    with mkdocs_gen_files.open(Path("api", doc_path), "w") as fd:
        fd.write(f"::: {'.'.join(parts)}\n")

    mkdocs_gen_files.set_edit_path(Path("api", doc_path), path)

with mkdocs_gen_files.open("api/SUMMARY.md", "w") as fd:
    fd.writelines(nav.build_literate_nav())
