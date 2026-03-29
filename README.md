# Bohrium Scholar EDM Toolkit

Open-source toolkit for two scholar email strategies: a topic-driven Bohrium AI Search share-link flow and a scholar-homepage email flow.

## Summary

This project helps teams build and test outbound emails for scholars with two different delivery strategies:

- `classic`: generate a topic from scholar context and one paper title, then send a Bohrium AI Search share-link email.
- `scholar`: fetch Bohrium scholar-page context and papers, then send a scholar-homepage email.

## Main Entrypoints

- `email_sender_en.py`: original batch runner for both strategies.
- `send_email.ts`: newer single-scholar preview/send flow for scholar emails.
- `app_server.ts`: local dashboard backend.
- `dashboard.html`: local dashboard UI.
- `scripts/scholar_email_scraper.py`: scrape candidate scholar emails from XLSX.
- `scripts/review_email_results.py`: QA pass for scraper outputs.

## Strategies

### 1. classic

Input:

- scholar name
- department / institution
- research interests
- one scholar paper title
- recipient email

Output:

- generated topic / scientific question
- AI Search research overview
- paper cards
- Bohrium share link

Template:

- `index_en.html`

### 2. scholar

Input:

- `scholarId`
- scholar page URL
- Chinese / English name
- research directions
- recipient email

Output:

- scholar-page URL
- personalized summary
- paper cards
- scholar stats

Templates:

- `index_scholar_cn_vA.html`
- `index_scholar_cn_vB.html`
- `email_template.html`

## Quick Start

### Python setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Node setup

No external runtime dependencies are required for the local TypeScript utilities.

### Environment

```bash
cp .env.example .env
```

Fill these before running:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `BOHRIUM_ACCESS_KEY`
- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SCHOLAR_API_BASE_URL`

## Run

### Batch mode

Classic:

```bash
python3 email_sender_en.py \
  --data data/test_invitation_single.csv \
  --config config_en.json \
  --campaign-mode classic \
  --delivery-mode smtp
```

Scholar:

```bash
python3 email_sender_en.py \
  --data data/test_invitation_single.csv \
  --config config_en.json \
  --campaign-mode scholar \
  --template-variant A \
  --paper-sort latest \
  --delivery-mode smtp
```

### Local dashboard

```bash
npm run console
```

Then open `http://127.0.0.1:8790`.

### Single scholar preview / send

```bash
npm run send:preview
npm run send:test
```

## Project Structure

```text
.
├── README.md
├── config_en.json
├── .env.example
├── requirements.txt
├── package.json
├── email_sender_en.py
├── send_email.ts
├── app_server.ts
├── dashboard.html
├── tracker.ts
├── tracking_server.ts
├── parse_upload.py
├── email_template.html
├── index_en.html
├── index_scholar_cn_vA.html
├── index_scholar_cn_vB.html
├── data/
│   └── test_invitation_single.csv
└── scripts/
    ├── scholar_email_scraper.py
    ├── review_email_results.py
    └── README_email_pipeline.md
```

## Security

This public copy removes tracked secrets and replaces environment-specific values with placeholders. You still need to rotate any credentials that were exposed in the original private workspace.

## License

MIT
