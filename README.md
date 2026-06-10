# OAK AI — CIS Academic Intelligence Platform

Flask web application for browsing and analysing Uzbek dissertations from oak.uz.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # set SESSION_SECRET, DATABASE_URL, GROQ_API_KEY
python scripts/create_tables.py
python app.py
```

## Daily Scraper (GitHub Actions)

The workflow `.github/workflows/daily_scraper.yml` runs every day at **06:00 UTC (11:00 Tashkent time)** and scrapes new dissertations from oak.uz into the database.

### Add DATABASE_URL secret to GitHub

1. Go to your GitHub repository
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `DATABASE_URL`
4. Value: your Neon PostgreSQL connection string  
   (e.g. `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`)
5. Click **Add secret**

The scraper will then have database access during each scheduled run.

### Manual run

Go to **Actions → Daily OAK Scraper → Run workflow** to trigger it manually.
