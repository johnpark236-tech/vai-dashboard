# VAI Dashboard

Static GitHub Pages frontend for the VAI read-only paper-trading dashboard.

This repository intentionally contains no SQLite database, trading ledger, API keys, OAuth secrets, Telegram token, Google Drive credentials, or real account numbers.

## Required Runtime Configuration

Edit `config.js` after creating a Google OAuth Web Client:

- `apiBase`: HTTPS URL for the VAI dashboard API.
- `googleClientId`: Google OAuth Client ID for Google Identity Services.

The backend must allow the Pages origin and must have:

- `VAI_GOOGLE_CLIENT_ID`
- `VAI_GOOGLE_ALLOWED_SUBS`
- `VAI_DASHBOARD_ALLOWED_ORIGINS=https://johnpark236-tech.github.io`

