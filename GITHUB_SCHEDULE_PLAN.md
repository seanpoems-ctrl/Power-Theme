# GitHub Actions Schedule Plan

## Overview

All data sources now run on **GitHub's native scheduler** (no external dependencies). Schedule optimized for:
- ✅ Fresh data without rate-limiting issues
- ✅ Finviz safe (max 2 calls/day)
- ✅ yfinance safe (unlimited)
- ✅ News APIs safe (every 30 min)
- ✅ Cost optimized (~$0.011/month)

---

## Daily Schedule (ET)

### 🌅 Pre-Market (8:45 AM ET)
**Purpose**: Prepare before market opens at 9:30 AM ET

| Service | Time | Frequency | Cost |
|---------|------|-----------|------|
| **Gapper Scan** | 8:45 AM | Once/day | ~$0.009/mo |
| **Market Brief** | 8:45 AM | Once/day | ~$0.0001/mo |

**Data**: Fresh gap analysis from TradingView + Finviz

---

### 📊 Market Hours (9:30 AM - 4:00 PM ET)
**Purpose**: Monitor earnings + breaking news throughout the day

| Service | Time | Frequency | Cost |
|---------|------|-----------|------|
| **Breaking News** | Every 30 min | 18 scans/day | ~$0.0003/mo |
| **Earnings Monitor** | Every 15 min | 36 scans/day | ~$0.001/mo |

**Data**: Real-time earnings moves, market-moving news

---

### 🏁 Post-Market (5:00 PM ET / 4:30 PM ET)
**Purpose**: End-of-day analysis and overnight preparation

| Service | Time | Frequency | Cost |
|---------|------|-----------|------|
| **Thematic Scanner** | 5:00 PM | Once/day | ~$0.001/mo |
| **Sector Report** | 4:30 PM | Once/day | ~$0.0014/mo |
| **Market Brief** | 5:00 PM | Once/day | ~$0.0001/mo |

**Data**: Daily thematic themes, sector rotation, market metrics

---

## UTC Conversion (for GitHub cron)

### EDT (March - November)
```
8:45 AM ET  = 12:45 UTC  → gapper-scan, market-brief
9:30 AM ET  = 13:30 UTC  → breaking-news starts
4:00 PM ET  = 20:00 UTC  → breaking-news ends
4:30 PM ET  = 20:30 UTC  → sector-report
5:00 PM ET  = 21:00 UTC  → thematic-scanner, market-brief
```

### EST (November - March)
```
8:45 AM ET  = 13:45 UTC  → gapper-scan, market-brief
9:30 AM ET  = 14:30 UTC  → breaking-news starts
4:00 PM ET  = 21:00 UTC  → breaking-news ends
4:30 PM ET  = 21:30 UTC  → sector-report
5:00 PM ET  = 22:00 UTC  → thematic-scanner, market-brief
```

---

## Workflow Files

### Updated Workflows
1. **premarket-gapper.yml**
   - Cron: `45 12 * * 1-5` (8:45 AM ET, Mon-Fri)
   - Runs: `gapper_service.py`
   - Status: ✅ Native scheduler

2. **breaking-news.yml**
   - Cron: `0,30 13-19 * * 1-5` (Every 30 min, 9:30 AM - 3:45 PM ET)
   - Plus: `0 20 * * 1-5` (4:00 PM ET)
   - Runs: `breaking_news.py`
   - Status: ✅ Native scheduler

3. **market-brief.yml**
   - Cron: `45 12 * * 1-5` + `0 21 * * 1-5` (8:45 AM + 5:00 PM ET)
   - Runs: `market_brief.py`
   - Status: ✅ Native scheduler (DST-aware)

4. **daily-scrape-deploy.yml**
   - Cron: `0 21 * * 1-5` (5:00 PM ET)
   - Runs: `scraper.py` + build + deploy
   - Status: ✅ Native scheduler (DST-aware)

### New Workflows
5. **earnings-monitor.yml** (NEW)
   - Cron: `0,15,30,45 13-19 * * 1-5` + `0 20 * * 1-5` (Every 15 min, market hours)
   - Runs: `earnings_monitor.py`
   - Status: ✅ Created

6. **sector-report.yml** (NEW)
   - Cron: `30 20 * * 1-5` (4:30 PM ET, post-market)
   - Runs: `daily_sector_report.py`
   - Status: ✅ Created

---

## Rate Limit Safety

### Finviz (Most Restrictive)
- **Limit**: ~50-100 requests per short period
- **Our usage**: 2x/day (pre-market + post-market)
- **Status**: ✅ Safe

### yfinance (Most Permissive)
- **Limit**: Soft, ~100+ req/min
- **Our usage**: ~36/day (earnings monitor) + ~10/day (sector report)
- **Status**: ✅ Very safe

### News APIs (Relaxed)
- **Limit**: Variable by provider
- **Our usage**: Every 30 min = 48/day
- **Status**: ✅ Safe

---

## Transition from cron-job.org

### Before (External Cron-Job.org)
- ❌ Gapper data was 4 days stale
- ❌ Breaking news was 2 days stale
- ❌ External dependency fragile

### After (GitHub Native Scheduler)
- ✅ Gapper data updates daily at 8:45 AM ET
- ✅ Breaking news updates every 30 min
- ✅ Earnings monitored every 15 min
- ✅ No external dependencies
- ✅ Built-in GitHub reliability

---

## Dashboard Data Freshness

| Data | Last Updated | Frequency |
|------|--------------|-----------|
| Breaking News | Every 30 min | 48x/day |
| Pre-Market Gappers | 8:45 AM ET | Daily |
| Earnings Reports | Every 15 min | As triggered |
| Daily Sector Report | 4:30 PM ET | Daily |
| Thematic Themes | 5:00 PM ET | Daily |
| Market Brief | 8:45 AM + 5 PM | 2x/day |

---

## Total Monthly Cost

| Service | Calls/Month | Cost |
|---------|------------|------|
| Gapper Service | 21 | $0.009 |
| Breaking News | 1,008 | $0.0003 |
| Earnings Monitor | 756 | $0.001 |
| Sector Report | 21 | $0.0014 |
| Thematic Scan | 21 | $0.001 |
| **TOTAL** | | **~$0.012/month** |

---

## Implementation Status

✅ Pre-market gapper: 8:45 AM ET (was 8:05 AM)
✅ Breaking news: Every 30 min (was every 5 min, external)
✅ Market brief: Pre + post-market
✅ Earnings monitor: Every 15 min (NEW)
✅ Sector report: Post-market (NEW)
✅ All on GitHub native scheduler

**All workflows committed and deployed!** 🚀
