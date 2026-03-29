# Scholar Email Pipeline

## 1) Scrape emails

```bash
python3 scripts/scholar_email_scraper.py \
  --input /path/to/your/source.xlsx \
  --out-dir outputs \
  --max-pages 4 \
  --retries 1 \
  --delay 0.2
```

Optional:

- `--limit 50`: only run first 50 rows for testing.
- `--enable-search`: add search-engine fallback (slower, more requests).
- `--timeout 8`: HTTP timeout seconds.

Main output files:

- `outputs/scholar_emails_<timestamp>.csv`: final picked email per scholar.
- `outputs/scholar_email_candidates_<timestamp>.csv`: all candidates and scores.
- `outputs/scrape_summary_<timestamp>.json`: summary metrics.

## 2) Run review program

```bash
python3 scripts/review_email_results.py \
  --input outputs/scholar_emails_<timestamp>.csv \
  --out-dir outputs \
  --low-conf-threshold 45 \
  --sample-size 80
```

Review outputs:

- `outputs/review_findings_<timestamp>.csv`: all risky rows needing review.
- `outputs/review_manual_sample_<timestamp>.csv`: random QA sample to manually check.
- `outputs/review_summary_<timestamp>.txt`: review stats and file pointers.

## 3) Suggested workflow

1. Test with `--limit 20`.
2. Check `review_findings` and tune threshold/strategy.
3. Run full 1000 records.
4. Manual-check all findings + random sample.

## Notes

- Script uses only Python standard library (no pandas/openpyxl required).
- Output CSV uses UTF-8 with BOM (`utf-8-sig`) for better Excel compatibility.
