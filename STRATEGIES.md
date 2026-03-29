# Strategies

## classic

Topic/share-link strategy.

Input:

- scholar name
- institution or department
- research interests
- one scholar paper title
- recipient email

Output:

- generated topic
- Bohrium AI Search share link
- research overview block
- related paper cards

Main files:

- `email_sender_en.py`
- `index_en.html`

## scholar

Scholar-homepage strategy.

Input:

- `scholarId`
- scholar homepage URL
- scholar name
- research directions
- recipient email

Output:

- normalized scholar page URL
- scholar summary
- scholar stats
- top paper cards

Main files:

- `email_sender_en.py`
- `send_email.ts`
- `email_template.html`
- `index_scholar_cn_vA.html`
- `index_scholar_cn_vB.html`
