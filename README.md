# Venetian Rate Watch

Checks The Venetian's nightly rate for a set of dates once a day, pushes a phone
alert when a night drops below your locked rate, and updates a little dashboard
anyone can open with a link.

- `venetian_watch.py` — the checker (fetches rates, alerts, writes `docs/rates.json`)
- `docs/index.html` — the dashboard (reads `docs/rates.json`)
- `docs/rates.json` — the latest rates, refreshed by the daily job
- `.github/workflows/watch.yml` — the daily schedule (GitHub Actions)

## One-time setup

1. **Create a new GitHub repo** and push these files to it (keep the folder
   structure exactly as-is).

2. **Set your details** in `venetian_watch.py`:
   - `LOCKED_RATE` — the nightly rate you're locked in at (from your confirmation).
   - `ADULTS` — match your actual reservation.
   - `TARGET_ROOM_TYPE` / `TARGET_RATE_PLAN` — optional; pin your exact booked room
     instead of tracking the cheapest available.
   - `NIGHTS` — your dates.

3. **Add your ntfy topic as a secret** (so it's not public in the code):
   - Install the **ntfy** app on your phone and subscribe to a topic name only you
     know, e.g. `venetian-rate-9f3k2x`.
   - In the repo: **Settings → Secrets and variables → Actions → New repository
     secret**. Name: `NTFY_TOPIC`. Value: your topic name.

4. **Turn on GitHub Pages** for the dashboard:
   - **Settings → Pages → Build and deployment → Source: Deploy from a branch**.
   - Branch: `main`, folder: `/docs`. Save.
   - After a minute your dashboard is live at
     `https://<your-username>.github.io/<your-repo>/` — that's the link to send
     your non-tech person. They open it, tap "Add to Home Screen," done.

5. **Test it now** without waiting for the schedule:
   - **Actions tab → Venetian rate watch → Run workflow.**
   - Watch the run. Green = it fetched rates, committed `rates.json`, and (if a
     night was below your lock) sent a phone alert. Red = something blocked or
     broke, and you'll get a failure alert on your phone too.

## Notes

- The daily time is **15:00 UTC** (~7–8 AM Pacific). Change the `cron` line in
  `watch.yml` to adjust. Times are always UTC.
- The job runs on GitHub's servers (datacenter IPs). If the token step starts
  getting blocked, move the checker to a home machine / Raspberry Pi on a schedule
  and have it push `rates.json` up instead — the dashboard side doesn't change.
- Phone alerts need the ntfy app installed and subscribed to your topic. Anyone
  who knows the topic name can send to it, so keep it obscure.
