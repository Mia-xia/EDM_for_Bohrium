#!/usr/bin/env python3
"""Review program for scholar email scraping outputs.

It flags risky records and creates a manual-check queue.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

EMAIL_RE = re.compile(r"(?i)^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def evaluate(main_rows: List[Dict[str, str]], low_conf_threshold: int) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    email_to_people = defaultdict(list)
    for row in main_rows:
        email = (row.get("抓取邮箱") or "").strip().lower()
        name = (row.get("英文姓名") or row.get("中文姓名") or "").strip()
        if email:
            email_to_people[email].append(name)

    findings: List[Dict[str, str]] = []
    counters = {
        "missing": 0,
        "invalid_format": 0,
        "low_confidence": 0,
        "duplicated_email": 0,
        "needs_review_status": 0,
    }

    for row in main_rows:
        email = (row.get("抓取邮箱") or "").strip().lower()
        confidence_raw = (row.get("置信度") or "0").strip()
        status = (row.get("状态") or "").strip()
        source = (row.get("邮箱来源") or "").strip()

        try:
            confidence = int(float(confidence_raw))
        except ValueError:
            confidence = 0

        issues: List[str] = []
        if not email:
            issues.append("missing_email")
            counters["missing"] += 1
        else:
            if not EMAIL_RE.match(email):
                issues.append("invalid_email_format")
                counters["invalid_format"] += 1

            if len(email_to_people[email]) > 1:
                issues.append("duplicated_email_for_multiple_scholars")
                counters["duplicated_email"] += 1

        if confidence < low_conf_threshold:
            issues.append("low_confidence")
            counters["low_confidence"] += 1

        if status in {"needs_review", "not_found"}:
            issues.append(f"status_{status}")
            counters["needs_review_status"] += 1

        if not source and email:
            issues.append("missing_source_url")

        if issues:
            findings.append(
                {
                    "中文姓名": row.get("中文姓名", ""),
                    "英文姓名": row.get("英文姓名", ""),
                    "抓取邮箱": row.get("抓取邮箱", ""),
                    "状态": status,
                    "置信度": str(confidence),
                    "邮箱来源": source,
                    "问题": ";".join(issues),
                    "建议": "人工核验该记录并确认邮箱归属",
                }
            )

    return findings, counters


def build_manual_sample(rows: List[Dict[str, str]], findings: List[Dict[str, str]], sample_size: int) -> List[Dict[str, str]]:
    flagged_keys = {
        (item.get("中文姓名", ""), item.get("英文姓名", ""), item.get("抓取邮箱", ""))
        for item in findings
    }

    eligible = []
    for row in rows:
        key = (row.get("中文姓名", ""), row.get("英文姓名", ""), row.get("抓取邮箱", ""))
        if key in flagged_keys:
            continue
        if not row.get("抓取邮箱", ""):
            continue
        eligible.append(row)

    random.shuffle(eligible)
    picks = eligible[: max(sample_size, 0)]
    out = []
    for row in picks:
        out.append(
            {
                "中文姓名": row.get("中文姓名", ""),
                "英文姓名": row.get("英文姓名", ""),
                "抓取邮箱": row.get("抓取邮箱", ""),
                "置信度": row.get("置信度", ""),
                "邮箱来源": row.get("邮箱来源", ""),
                "复核结果": "",
                "复核备注": "",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Review scraper output and generate QA files")
    parser.add_argument("--input", required=True, help="Main scraper CSV path")
    parser.add_argument("--out-dir", default="outputs", help="Output directory")
    parser.add_argument("--low-conf-threshold", type=int, default=45, help="Confidence threshold")
    parser.add_argument("--sample-size", type=int, default=80, help="Random sample size for manual audit")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input CSV not found: {input_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(input_path)
    findings, counters = evaluate(rows, args.low_conf_threshold)
    sample = build_manual_sample(rows, findings, args.sample_size)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    findings_csv = out_dir / f"review_findings_{ts}.csv"
    sample_csv = out_dir / f"review_manual_sample_{ts}.csv"
    summary_txt = out_dir / f"review_summary_{ts}.txt"

    findings_fields = ["中文姓名", "英文姓名", "抓取邮箱", "状态", "置信度", "邮箱来源", "问题", "建议"]
    sample_fields = ["中文姓名", "英文姓名", "抓取邮箱", "置信度", "邮箱来源", "复核结果", "复核备注"]

    write_csv(findings_csv, findings, findings_fields)
    write_csv(sample_csv, sample, sample_fields)

    total = len(rows)
    summary_lines = [
        f"review time: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"total records: {total}",
        f"flagged findings: {len(findings)}",
        f"manual sample size: {len(sample)}",
        f"missing emails: {counters['missing']}",
        f"invalid format: {counters['invalid_format']}",
        f"low confidence(<{args.low_conf_threshold}): {counters['low_confidence']}",
        f"duplicated emails: {counters['duplicated_email']}",
        f"needs_review/not_found status: {counters['needs_review_status']}",
        "",
        f"findings_csv: {findings_csv}",
        f"sample_csv: {sample_csv}",
    ]
    summary_txt.write_text("\n".join(summary_lines), encoding="utf-8")

    print("Review complete")
    print(f"findings_csv: {findings_csv}")
    print(f"sample_csv: {sample_csv}")
    print(f"summary_txt: {summary_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
