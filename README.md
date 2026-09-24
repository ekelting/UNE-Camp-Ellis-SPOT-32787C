# 🌊 Camp Ellis Wave Buoy: live data

**University of New England · Sofar Spotter SPOT-32787C · Camp Ellis, Saco Bay, Maine**

**📍 Live dashboard:** <https://ekelting.github.io/UNE-Camp-Ellis-SPOT-32787C/>

This repository runs the public dashboard for UNE's wave buoy off Camp Ellis beach, part of a coastal-erosion research project. It fetches new data from the buoy every hour and publishes it automatically.

The dashboard shows:

- **Waves:** height, period (rhythm), direction, storm events, the seasonal pattern, and the full wave spectrum
- **Weather at the buoy:** barometric pressure
- **Below the surface (Smart Mooring sensors):** water temperature at three depths, dissolved oxygen (concentration and % saturation), and current speed and direction
- **Buoy health:** battery and hull humidity

## Using the data

- **Spreadsheet and CSV downloads:** use the buttons at the top of the dashboard. The available files are `hourly.csv`, `daily.csv`, `monthly.csv`, `storms.csv` and `sensors_hourly.csv`.
- **A Google Sheet that stays current:** type this into any cell. Google refreshes it about every hour.
  ```
  =IMPORTDATA("https://ekelting.github.io/UNE-Camp-Ellis-SPOT-32787C/data/daily.csv")
  ```
- **Raw readings:** these live in the [`data/`](data) folder, as one plain CSV per day.
- **Times:** the dashboard and the CSV downloads use US Eastern time. The raw files use UTC.

## How the data is processed

- **Wave estimates:** the 15-minute "hdr" wave estimates are used first. The 30-minute onboard estimates fill any gaps.
- **Removed as glitches:**
  - single-reading wave spikes, e.g. one 9 m reading between two 0.6 m readings
  - peak periods over 22 s
  - positions more than 150 m from the mooring, and sensor readings from before the buoy was moored on 3 Nov 2025
- **Storm events:** hourly wave height of at least 1 m (3.3 ft) lasting 6+ hours.
- **Wave power:** uses the deep-water formula, with the energy period taken from each spectrum (≥ 0.04 Hz). At this water depth, treat it as a ballpark figure.
- **Wind:** this is the Spotter's estimate from the waves, not an anemometer reading.
- **Dissolved oxygen:** converted to mg/L by the sensor assuming a salinity of 35 ppt. Near the Saco River plume, the true values may be slightly higher.

## Credits

The data comes from [Sofar Ocean](https://www.sofarocean.com/) Spotter SPOT-32787C with Smart Mooring sensors, deployed by the University of New England.

**Sister site:** [Biddeford Pool wave buoy](https://ekelting.github.io/UNE-Biddeford-Pool-SPOT-32905C/)
