#!/usr/bin/env python3
import json
import sys
from pathlib import Path

import pandas as pd


COLUMN_ALIASES = {
    "profile_url": "profileUrl",
    "profileurl": "profileUrl",
    "学者主页": "profileUrl",
    "name_ch": "nameCh",
    "中文姓名": "nameCh",
    "name_en": "nameEn",
    "英文姓名": "nameEn",
    "direction": "direction",
    "研究方向": "direction",
    "institute_ch": "instituteCh",
    "中文机构": "instituteCh",
    "institute_en": "instituteEn",
    "英文机构": "instituteEn",
    "email": "email",
    "邮箱": "email",
    "recipient": "email",
    "scholarid": "scholarId",
    "scholar_id": "scholarId",
    "source_type": "sourceType",
    "third_author_link": "thirdAuthorLink",
}


def normalize_column(name: str) -> str:
    key = str(name).strip()
    return COLUMN_ALIASES.get(key, COLUMN_ALIASES.get(key.lower(), key))


def clean_value(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def main() -> None:
    if len(sys.argv) != 2:
      raise SystemExit("usage: parse_upload.py <file>")

    path = Path(sys.argv[1])
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    else:
        raise SystemExit(f"unsupported file type: {suffix}")

    df = df.rename(columns={column: normalize_column(column) for column in df.columns})
    rows = []
    for index, (_, row) in enumerate(df.iterrows(), start=1):
        item = {str(column): clean_value(value) for column, value in row.items()}
        item["rowNumber"] = index
        rows.append(item)

    payload = {
        "columns": list(df.columns),
        "rows": rows,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
