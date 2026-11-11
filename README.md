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


## VAI-RIPPLE 가상매매 (`ripple/`)

뉴스 기반 가상매매 시뮬레이션. 대시보드: https://johnpark236-tech.github.io/vai-dashboard/ripple/

- `ripple/PLAYBOOK.md` — 매일 1종목 가상매수 절차서
- `ripple/data/events.json` — 과거 뉴스 백테스트 사건 목록
- `ripple/data/trades.json` — 실전 가상매수 기록
- `ripple/scripts/update.py` — 가격 수집·백테스트·현황 계산 (GitHub Actions 가 평일 장 마감 후 실행)
- 규칙: 종목당 300만원, 총 투자금 1억원 한도, 익절·손절은 백테스트 기반 자동 설정(`ripple/data/auto.json`), 최장 86일
