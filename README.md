# Oxford Design Studio — Profitability Dashboard

FastAPI + PostgreSQL app for tracking designer and project profitability from Studio ERP exports.

## Stack
- **Backend**: Python / FastAPI
- **Database**: PostgreSQL (Railway)
- **Deploy**: Railway (backend + DB) + GitHub

## Local setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in DATABASE_URL and SECRET_KEY in .env

python seed.py --email admin@oxforddesign.com --password yourpassword
uvicorn main:app --reload
```

## Railway deploy

1. Push this repo to GitHub
2. In Railway: New Project → Deploy from GitHub repo
3. Add a PostgreSQL service to the project
4. Set environment variables:
   - `DATABASE_URL` → copy from Railway Postgres "Connect" tab
   - `SECRET_KEY` → any long random string (e.g. `openssl rand -hex 32`)
5. Deploy — Railway auto-detects the Procfile

## First-time setup after deploy

SSH into Railway or use the Railway shell to run:
```bash
python seed.py --email admin@oxforddesign.com --password yourpassword
```

This creates the designer roster, all historical client assignments, and your admin account.

## User roles

| Role   | Dashboard | Upload | Designers | Users |
|--------|-----------|--------|-----------|-------|
| admin  | ✓         | ✓      | ✓         | ✓     |
| viewer | ✓         | —      | —         | —     |

## Adding users

Log in as admin → Users → Add User. Set role to `viewer` for read-only access.
