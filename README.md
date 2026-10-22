# Finder

Finder watches the career pages of 300+ companies that pay freshers 12 LPA or more. When a new job matches your profile, it messages you on **WhatsApp** and by **email**, usually within an hour of the job going live. Every match also goes on a personal **dashboard**.

- **Tier 1–3 companies** (FAANG-level, HFT/quant, high-paying product) are checked **every hour**.
- **Tier 4–5 companies** are checked **every 3 hours**.
- **Matching:** the job title must be a fresher software role, the location must be in India or remote-from-India, and the description must not ask for 2+ years of experience.

## Files

| File | What it is |
|---|---|
| `companies.json` | The watchlist: company, tier, careers page, how to read it, estimated fresher CTC |
| `companies.csv` | The same list as a spreadsheet |
| `profile.json` | Your filters: role keywords, excluded words, locations, max experience, skills |
| `finder.py` | The program |
| `core/` | Readers for Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Eightfold, Amazon, Atlassian, and a headless-browser reader for custom career sites |
| `docs/` | The dashboard (served by GitHub Pages) plus `jobs.json` and `status.json` |
| `state/` | The jobs Finder has already seen, so you never get the same alert twice |
| `.github/workflows/finder.yml` | The hourly schedule |

## One-time setup (about 15 minutes)

### 1. Turn on WhatsApp alerts (free, CallMeBot)

1. Save **+34 623 78 64 49** in your phone's contacts (name it "CallMeBot").
2. Send it this WhatsApp message: `I allow callmebot to send me messages`
3. It replies with your **API key**. Keep it for step 4.

(Telegram still works too if you add its two secrets, but it's optional.)

### 2. Get a Gmail app password (for email alerts)

1. Turn on 2-Step Verification on your Google account.
2. Go to **myaccount.google.com/apppasswords** and create an app password named "Finder".
3. Copy the 16-character password.

### 3. Put the code on GitHub

Make the repository **public**. That gives you unlimited free GitHub Actions minutes and free GitHub Pages. A private repo would use up the free 2,000 minutes a month in about a week. Your secrets stay private either way.

```bash
cd ~/Projects/Finder
git init && git add . && git commit -m "Finder"
gh repo create Finder --public --source . --push      # or create the repo on github.com and push
```

### 4. Add secrets

On GitHub, open the repo, then go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|---|---|
| `WHATSAPP_PHONE` | your number with country code, e.g. `+919876543210` |
| `WHATSAPP_APIKEY` | the key CallMeBot sent you |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | the 16-character app password |
| `EMAIL_TO` | where alerts should go (can be the same Gmail address) |

### 5. Turn on the dashboard

Go to **Settings → Pages**, then set **Source** to "Deploy from a branch", **Branch** to `main`, and the folder to `/docs`. Your dashboard will be at `https://<your-username>.github.io/Finder/`.

### 6. First run

Go to **Actions → Finder → Run workflow** and set mode to `--validate`. This first run:

- checks every company once,
- records all current jobs as already seen, so you don't get hundreds of alerts,
- fills the dashboard,
- and shows which career pages worked on the **Companies** tab.

After that, the hourly schedule runs on its own and only new jobs trigger alerts.

## Changing what you get

- **Different roles or keywords:** edit `include_title` and `exclude_title` in `profile.json`.
- **Internships** are ignored.
- **Alerts and dashboard history** keep the last 10 days.
- **Experience:** jobs asking for up to 2 years are included (`"max_years_required": 2`).
- **Add a company:** add an entry to `companies.json`. The `source` can be any of these:
  - `{"type":"greenhouse","token":"stripe"}`
  - `{"type":"lever","slug":"cred"}`
  - `{"type":"ashby","org":"sarvam"}`
  - `{"type":"workday","tenant":"nvidia","wd":"wd5","site":"NVIDIAExternalCareerSite"}`
  - `{"type":"smartrecruiters","id":"Visa"}`
  - `{"type":"page","url":"https://careers.example.com/jobs?location=India"}` for anything else
- **Pause a company:** add `"enabled": false` to its entry.

## Running on your Mac instead

```bash
cd ~/Projects/Finder
pip3 install playwright && python3 -m playwright install chromium
export WHATSAPP_PHONE=+91... WHATSAPP_APIKEY=...
python3 finder.py --validate        # first time
python3 finder.py                   # each run after that
```

## Notes

- Tier and CTC figures are research estimates from levels.fyi, AmbitionBox, LeetCode and Blind data plus judgement. Treat them as a guide, not an offer.
- About half the companies use custom career sites. Finder reads those with a headless browser and collects the job links it sees there. Some of those sites change often or block bots. After the first run, the Companies tab shows which ones failed.
- Some boards also have campus or referral variants that Finder could watch as extra sources (for example `growwreferrals` and `zyngaearlycareers`).
