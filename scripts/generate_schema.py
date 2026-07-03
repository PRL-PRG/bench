"""Generate the JSON Schema for the serialized `Report` model.

Dev-only tool. Requires the `schema` dependency group (`uv run --group schema`).
The schema describes the *full* serialized form. Note that
`report_to_json(..., include_output=False)` (the default) drops the
`stdout`, `stderr`, and `env` fields, so reports written with the defaults are
a valid subset of this schema.
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from bench.core.sample import Report

OUTPUT = Path(__file__).resolve().parent.parent / "schema.json"


def main() -> None:
    schema = TypeAdapter(Report).json_schema()
    OUTPUT.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
