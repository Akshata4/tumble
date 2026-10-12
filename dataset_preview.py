"""Build a compact text preview of an uploaded dataset for the system
prompt -- enough for the agent to write its own baseline train.py without
ever seeing the full (possibly large) CSV."""

import pandas as pd


def build_preview(csv_path: str, target_column: str, max_rows: int = 5) -> str:
    df = pd.read_csv(csv_path)

    if target_column not in df.columns:
        raise ValueError(f"target column '{target_column}' not found in dataset columns: {list(df.columns)}")

    lines = [
        f"Shape: {df.shape[0]} rows x {df.shape[1]} columns",
        "",
        "Columns (name: dtype):",
    ]
    for col, dtype in df.dtypes.items():
        marker = "  <- target" if col == target_column else ""
        lines.append(f"  {col}: {dtype}{marker}")

    lines += ["", f"First {max_rows} rows:", df.head(max_rows).to_csv(index=False).strip()]

    target = df[target_column]
    lines.append("")
    if target.dtype == "object" or target.nunique() <= 20:
        lines.append(f"Target ('{target_column}') value counts:")
        lines.append(target.value_counts().to_string())
    else:
        lines.append(f"Target ('{target_column}') summary statistics:")
        lines.append(target.describe().to_string())

    null_counts = df.isnull().sum()
    nulls = null_counts[null_counts > 0]
    if not nulls.empty:
        lines += ["", "Columns with missing values:", nulls.to_string()]

    return "\n".join(lines)
