# 🌊 Camp Ellis Wave Buoy — live dashboard

University of New England · Sofar Spotter **SPOT-32787C** · Camp Ellis, Saco Bay, Maine

Every hour, GitHub pulls the latest data from Sofar and saves it here, then rebuilds and publishes the website:

- **Waves:** height, period, direction, swell/sea split and full spectra
- **Weather at the buoy:** barometric pressure and the wind estimate
- **Buoy health:** battery, solar and hull humidity
- **Smart Mooring sensors:** subsurface water temperature, dissolved oxygen (concentration and % saturation), current speed and direction, plus any other sensors on the line

**📍 Dashboard:** <https://ekelting.github.io/UNE-Camp-Ellis-SPOT-32787C/>
**🗂️ Repository:** <https://github.com/ekelting/UNE-Camp-Ellis-SPOT-32787C>
**📊 Spreadsheet:** the "Spreadsheet (.xlsx)" button at the top of the dashboard

---

## One-time setup (≈10 minutes, all in the browser)

1. **Create the repository**
   1. On github.com, click **＋ → New repository**.
   2. Name it exactly `UNE-Camp-Ellis-SPOT-32787C` and choose **Public**. The free tier of GitHub Pages needs a public repo. The buoy's Sofar page is already public, and your token stays secret either way.
   3. Leave "Add a README" **unticked**, then click **Create repository**.

2. **Upload these files**
   1. On the new repo's page, click **uploading an existing file**.
   2. Drag in *everything* from this folder, including the `.github` folder.
   3. Click **Commit changes**.

   > If the `.github` folder didn't upload (some browsers skip folders starting with a dot):
   > 1. Click **Add file → Create new file**.
   > 2. Type the name `.github/workflows/update-buoy.yml`.
   > 3. Paste in the contents of that file from this folder, then commit.

3. **Add your Sofar API token as a secret**
   1. Get the token at <https://spotter.sofarocean.com/api> (signed in to the account that owns the buoy).
   2. In the repo, open **Settings → Secrets and variables → Actions → New repository secret**.
   3. Name: `SOFAR_API_TOKEN`. Value: paste the token. Click **Add secret**.

   GitHub encrypts the secret, and it is never shown in logs or on the website.

4. **Turn on the website:** go to **Settings → Pages → Build and deployment → Source: GitHub Actions**.

5. **Run it the first time**
   1. Open the **Actions** tab and click **"I understand my workflows, go ahead and enable them"** if GitHub asks.
   2. Click **Update buoy dashboard → Run workflow**.
   3. The first run back-fills everything since September 1, and every Smart Mooring reading since the November 3 launch. It can take 10–20 minutes.

   When the run finishes, the dashboard is live at **https://ekelting.github.io/UNE-Camp-Ellis-SPOT-32787C/**.

That's it: it now updates **every hour, forever, without anyone's computer switched on**.

---

## Everyday use

- **To see the data,** open the dashboard link. Every chart has hover details, zoom, and a feet/metres switch.
- **To force an update now,** go to **Actions → Update buoy dashboard → Run workflow**.
- **To get the spreadsheet,** use the button on the dashboard. In Drive, open it with **Open with → Google Sheets**.
- **To keep a Google Sheet that updates itself,** type this in any cell. Google refreshes it about every hour:

  ```
  =IMPORTDATA("https://ekelting.github.io/UNE-Camp-Ellis-SPOT-32787C/data/daily.csv")
  ```

  The site publishes `daily.csv`, `hourly.csv`, `monthly.csv`, `storms.csv` and `sensors_hourly.csv`.
- **Raw data** lives in `data/`, as one plain CSV per day. It works in Excel, R, MATLAB and Python.

## What's in here

| Path | What it is |
|---|---|
| `.github/workflows/update-buoy.yml` | The hourly schedule: fetch, save, build, publish |
| `buoy/fetch.py` | Asks Sofar's API for anything new. It resumes where it left off and skips nothing, even after an outage. |
| `buoy/build.py` | Builds `site/`: the dashboard, spreadsheet and CSV exports |
| `buoy/buoy_core.py` | Loads and cleans data, and computes statistics, storm events and plain-English findings. **Settings are at the top.** |
| `buoy/sensors.py` | Smart Mooring handling. It recognises each channel (temperature, oxygen, currents…) automatically. |
| `buoy/make_dashboard.py`, `buoy/make_spreadsheet.py` | Page and spreadsheet layout |
| `data/seed/` | History from the Spotter CSV downloads (Nov 3 2025 → Aug 31 2026). It includes battery and humidity readings the API only gives "right now". |
| `data/live/` | Everything fetched from the API, one file per day |
| `data/sensors/` | Smart Mooring readings, one file per day |
| `data/state.json` | How far each data stream has been downloaded |

**Adding old CSV downloads:** drop any Spotter `…embedded-history.csv` (or `.csv.gz`) into `data/seed/`. Keep `SPOT-32787C` in the file name. It gets merged, and duplicates are removed.

**Changing the storm definition, calm threshold and so on:** edit the settings block at the top of `buoy/buoy_core.py`. Committing the change triggers a rebuild.

## Troubleshooting

- **Red ❌ run with "Sofar refused the API token":** the secret is missing or wrong, or the account can't see this buoy. Re-add `SOFAR_API_TOKEN`.
- **"Permission denied" when the run pushes:**
  1. Open **Settings → Actions → General → Workflow permissions**.
  2. Choose **Read and write permissions**, then save.
- **The dashboard says the buoy hasn't reported:** check the buoy on the Spotter dashboard. The page catches up automatically once data flows again.
- **Smart Mooring section is empty:** Sofar returned no sensor readings for this token. Check that the Smart Mooring is set up on the account. The section fills in on the next hourly run after data appears.
- **Hourly runs stopped:** GitHub pauses schedules after 60 days without repository activity. The hourly data commits normally count as activity. If the schedule is ever paused, **Actions → Update buoy dashboard → Enable workflow** turns it back on.

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
SOFAR_API_TOKEN=xxxx python buoy/fetch.py    # get new data
python buoy/build.py --inline-plotly         # build site/ (open site/index.html)
```

## Data notes

- **Wave estimates:** the 15-minute "hdr" estimates are used first, and the 30-minute onboard estimates fill any gaps.
- **Removed as glitches:**
  - single-reading wave spikes
  - peak periods over 22 s
  - positions more than 150 m from the mooring
- **Wave power** uses the deep-water formula, with the energy period taken from each spectrum (≥ 0.04 Hz). At this depth, treat it as a ballpark.
- **Wind** is the Spotter's estimate from the waves, not an anemometer.
- **Smart Mooring outliers:** readings more than 6 robust standard deviations from their neighbours are dropped. The count per channel is in the sensor table.
- **Dissolved oxygen thresholds** are drawn at 5 mg/L (stress) and 2 mg/L (hypoxia).
