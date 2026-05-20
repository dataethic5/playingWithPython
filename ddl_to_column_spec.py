"""
Author: Jonathan Muller
Date: 2026/04/26

ddl_to_column_spec_v2.py
Converts T-SQL DDL (DROP/GO/CREATE format) to a dbldatagen Python spec file.

Usage:
    from ddl_to_column_v2_spec import tsql_file_to_py
    tsql_file_to_py(sql_directory, py_directory)
"""

import re
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent.resolve()

TABLE_RE = re.compile(r"CREATE TABLE \[dbo\]\.\[(.*?)\]", re.IGNORECASE)
COL_RE   = re.compile(r"\[(.*?)\]\s+\[(.*?)\]", re.IGNORECASE)

FAKER_BY_NAME = [
    (r"firstname|first_name",                              "first_name"),
    (r"lastname|last_name|surname",                        "last_name"),
    (r"fullname|full_name",                                "name"),
    (r"username|user_name",                                "user_name"),
    (r"email|contactemail",                                "email"),
    (r"phone|mobile",                                      "phone_number"),
    (r"company|supplier|suppliername",                     "company"),
    (r"address",                                           "address"),
    (r"city",                                              "city"),
    (r"postcode|zipcode",                                  "postcode"),
    (r"country",                                           "country"),
    (r"state|county",                                      "state"),
    (r"street",                                            "street_address"),
    (r"description|desc",                                  "sentence"),
    (r"status|source|tag|label|category|code|type|name",   "word"),
]

SKIP_KEYWORDS = {"constraint", "primary", "unique", "foreign", "check", "index"}


def _faker_for_col(col_name, col_type):
    typ = col_type.upper()

    if "INT" in typ:
        return {"random": True, "minValue": 1, "maxValue": 10000}

    if typ in ("DECIMAL", "NUMERIC", "MONEY", "FLOAT"):
        return {"random": True, "minValue": 1.0, "maxValue": 1000.0}

    if typ in ("DATETIME2", "DATETIME", "SMALLDATETIME"):
        return {"random": True, "text": "__FAKER__date_time__FAKER__"}

    if typ == "DATE":
        return {"random": True, "text": "__FAKER__date__FAKER__"}

    if typ == "TIME":
        return {"random": True, "text": "__FAKER__time__FAKER__"}

    if typ == "BIT":
        return {"random": True, "values": [0, 1], "weights": [0.5, 0.5]}

    if typ == "UNIQUEIDENTIFIER":
        return {"random": True, "text": "__FAKER__integer__FAKER__"}

    if any(t in typ for t in ("VARCHAR", "CHAR", "TEXT", "NVARCHAR")):
        col_lower = col_name.lower()
        for pattern, faker_fn in FAKER_BY_NAME:
            if re.search(pattern, col_lower):
                return {"random": True, "text": f"__FAKER__{faker_fn}__FAKER__"}
        return {"random": True, "text": "__FAKER__word__FAKER__"}

    return {"random": True, "text": "__FAKER__word__FAKER__"}


def _format_value(v):
    """Format a single value for Python output."""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, str):
        # Unwrap fakerText marker — write as bare function call
        m = re.match(r"^__FAKER__(.+)__FAKER__$", v)
        if m:
            return f'fakerText("{m.group(1)}")'
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(_format_value(i) for i in v)
        return f"[{items}]"
    if isinstance(v, float):
        return str(v)
    return str(v)


def _format_col_spec(spec, indent=12):
    """Format a column spec dict onto one or multiple lines."""
    pad = " " * indent
    items = []
    for k, v in spec.items():
        items.append(f'{pad}"{k}": {_format_value(v)}')
    return "{\n" + ",\n".join(items) + "}"


def _format_table_specs(table_specs):
    """Build the full table_specs = { ... } string."""
    lines = ["table_specs = {"]
    tables = list(table_specs.items())
    for t_idx, (table, cols) in enumerate(tables):
        lines.append(f'    "{table}": {{')
        col_items = list(cols.items())
        for c_idx, (col, spec) in enumerate(col_items):
            comma = "," if c_idx < len(col_items) - 1 else ""
            lines.append(f'        "{col}": {_format_col_spec(spec)}{comma}')
        table_comma = "," if t_idx < len(tables) - 1 else ""
        lines.append(f"    }}{table_comma}")
    lines.append("}")
    return "\n".join(lines)


def tsql_ddl_to_dbldatagen_spec(batch):
    """Parse a single CREATE TABLE batch and return a table_specs dict."""
    m = TABLE_RE.search(batch)
    if not m:
        return {}

    table_name = m.group(1)
    start = batch.find("(", m.end())
    if start == -1:
        return {}

    # Balance parens to find closing paren
    balance = 0
    end = start
    for i, ch in enumerate(batch[start:], start):
        if ch == "(":
            balance += 1
        elif ch == ")":
            balance -= 1
            if balance == 0:
                end = i
                break

    cols = {}
    for line in batch[start:end].splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        col_match = COL_RE.search(line)
        if col_match:
            col_name, col_type = col_match.groups()
            if col_name.lower() in SKIP_KEYWORDS:
                continue
            cols[col_name] = _faker_for_col(col_name, col_type)

    return {f"dbo.{table_name}": cols}


def _format_single_table_spec(table_name, cols):
    """Build a single-table table_specs = { ... } string."""
    return _format_table_specs({table_name: cols})


def _safe_table_filename(table_name):
    """Convert dbo.TableName -> TableName.py-safe stem."""
    return table_name.split(".")[-1]


def tsql_file_to_py(sql_dir, py_dir):
    """
    Read .sql file(s) from a directory and write one dbldatagen table_specs .py
    file per table into the output directory.

    Parameters
    ----------
    sql_dir : str
        Directory containing .sql file(s), relative to this script.
    py_dir : str
        Directory where output .py files will be written, relative to this script.

    Returns
    -------
    dict
        Combined table_specs dictionary across all discovered tables.
    """

    sql_path = SCRIPT_DIR/sql_dir
    py_path = SCRIPT_DIR/py_dir
    py_path.mkdir(parents=True, exist_ok=True)

    table_specs = {}

    for sql_file in sorted(sql_path.glob("*.sql")):
        raw = sql_file.read_text(encoding="utf-8")
        batches = re.split(r"\n\s*GO\s*\n", raw, flags=re.I)

        for batch in batches:
            if re.search(r"CREATE\s+TABLE", batch, re.IGNORECASE):
                spec = tsql_ddl_to_dbldatagen_spec(batch)
                table_specs.update(spec)

                for table_name, cols in spec.items():
                    file_stem = _safe_table_filename(table_name)
                    out_file = py_path / f"{file_stem}.py"

                    py_content = (
                        "# Auto magic dbldatagen table specs generation\n"
                        f"# Date: {datetime.now()}\n"
                        f"# Source: {sql_file.relative_to(SCRIPT_DIR)}\n\n"
                        + _format_single_table_spec(table_name, cols)
                        + "\n"
                    )

                    out_file.write_text(py_content, encoding="utf-8")
                    print(f"Written to {out_file}")

    return table_specs