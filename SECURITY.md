# Security

This public directory is a sanitized export of a private working project.

What was removed or replaced:

- SMTP credentials
- Bohrium access keys
- Google credential file names
- local absolute paths
- UAT / test-only service endpoints
- real sample email addresses

Before using this project in any environment:

1. create a `.env` file from `.env.example`
2. provide your own credentials
3. verify all target endpoints
4. rotate any credentials that were previously exposed in the private workspace
