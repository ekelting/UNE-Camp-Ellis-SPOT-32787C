"""
fetch.py — pulls new data for the buoy from Sofar's API and saves it under data/.

Runs every hour on GitHub Actions (see .github/workflows/update-buoy.yml). It only asks
Sofar for what it doesn't have yet, so the first run back-fills history (can take a while)
and later runs take seconds.

What it saves (all plain CSV, one file per UTC day so git history stays small):
  data/live/YYYY/SPOT-xxxx_waves_YYYY-MM-DD.csv   waves + wind + partitions + spectra (+ surface temp)
  data/live/YYYY/SPOT-xxxx_baro_YYYY-MM-DD.csv    barometric pressure
  data/live/SPOT-xxxx_status.csv                  battery / solar / humidity, one row per run
  data/sensors/YYYY/sensors_YYYY-MM-DD.csv        Smart Mooring (Bristlemouth) sensor readings, long format
  data/state.json                                 how far each stream has been downloaded

Columns use the same names as the Spotter dashboard's CSV export, so the analysis code
reads live data and old CSV downloads the same way.

Usage:  SOFAR_API_TOKEN=xxxx python buoy/fetch.py [--max-minutes 40]
"""
import argparse
import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buoy_core as bc  # noqa: E402

API = 'https://api.sofarocean.com/api'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
STATE = os.path.join(DATA, 'state.json')
DEPLOYED = '2025-11-03T00:00:00Z'      # buoy launch — sensor back-fill starts here
SEED_END = '2026-09-01T04:00:00Z'      # the committed CSV seed covers waves up to here
OVERLAP = pd.Timedelta(hours=3)        # re-ask for a little overlap each run (late-arriving data)

WAVE_COLS = {'significantWaveHeight': 'Significant Wave Height (m)', 'peakPeriod': 'Peak Period (s)',
             'meanPeriod': 'Mean Period (s)', 'peakDirection': 'Peak Direction (deg)',
             'peakDirectionalSpread': 'Peak Directional Spread (deg)', 'meanDirection': 'Mean Direction (deg)',
             'meanDirectionalSpread': 'Mean Directional Spread (deg)'}
PART_KEYS = [('startFrequency', 'Start Frequency (hz)'), ('endFrequency', 'End Frequency (hz)'),
             ('significantWaveHeight', 'Significant Wave Height (m)'), ('meanPeriod', 'Mean Period (s)'),
             ('meanDirection', 'Mean Direction (deg)'), ('meanDirectionalSpread', 'Mean Directional Spread (deg)')]


# ---------------------------------------------------------------- helpers --
def iso(t):
    return pd.Timestamp(t).tz_convert('UTC').strftime('%Y-%m-%dT%H:%M:%SZ')


def ts(x):
    return pd.Timestamp(x).tz_convert('UTC') if pd.Timestamp(x).tzinfo else pd.Timestamp(x).tz_localize('UTC')


def epoch(x):
    return int(ts(x).timestamp())


class Sofar:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers['token'] = token
        self.calls = 0

    def get(self, path, **params):
        for attempt in range(6):
            try:
                r = self.s.get(f'{API}/{path}', params=params, timeout=90)
            except requests.RequestException as e:
                wait = 5 * (attempt + 1)
                print(f'   network hiccup ({e.__class__.__name__}), retrying in {wait}s')
                time.sleep(wait)
                continue
            self.calls += 1
            if r.status_code in (401, 403):
                raise SystemExit(f'❌ Sofar refused the API token (HTTP {r.status_code}) for {path}. '
                                 f'Check the SOFAR_API_TOKEN secret and that this account can see {bc.SPOTTER_ID}.')
            if r.status_code == 429 or r.status_code >= 500:
                wait = 10 * (attempt + 1)
                print(f'   Sofar busy (HTTP {r.status_code}), retrying in {wait}s')
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(0.25)  # be polite
            return r.json()
        raise RuntimeError(f'Sofar API kept failing for {path}')

    def paged(self, key, start, end, limit, **flags):
        """Get one array of /wave-data for [start, end). If a reply hits the `limit` cap, the window is
        split in half and each half fetched again, so nothing is missed whatever order Sofar returns."""
        start, end = ts(start), ts(end)
        j = self.get('wave-data', spotterId=bc.SPOTTER_ID, startDate=iso(start), endDate=iso(end),
                     limit=limit, **flags)
        items = (j.get('data') or {}).get(key) or []
        if len(items) >= limit and end - start > pd.Timedelta(minutes=20):
            mid = start + (end - start) / 2
            return self.paged(key, start, mid, limit, **flags) + self.paged(key, mid, end, limit, **flags)
        return items


def load_state():
    st = {}
    if os.path.exists(STATE):
        with open(STATE) as fh:
            st = json.load(fh)
    st.setdefault('waves', SEED_END)
    st.setdefault('baro', SEED_END)
    st.setdefault('spectra', SEED_END)
    st.setdefault('sensors', DEPLOYED)
    return st


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, 'w') as fh:
        json.dump(st, fh, indent=2, sort_keys=True)


def merge_daily(df, kind, keys, folder='live', name=None):
    """Append rows to one CSV per UTC day, de-duplicating on `keys` (newest wins)."""
    if df.empty:
        return 0
    df = df.copy()
    tcol = 'Epoch Time' if 'Epoch Time' in df else 'timestamp'
    day = pd.to_datetime(df[tcol], unit='s' if tcol == 'Epoch Time' else None, utc=True).dt.strftime('%Y-%m-%d')
    n = 0
    for d, part in df.groupby(day.values):
        sub = os.path.join(DATA, folder, d[:4])
        os.makedirs(sub, exist_ok=True)
        fn = os.path.join(sub, (name or f'{bc.SPOTTER_ID}_{kind}') + f'_{d}.csv')
        if os.path.exists(fn):
            part = pd.concat([pd.read_csv(fn, dtype=str), part.astype(str)], ignore_index=True)
        part = part.astype(str).replace({'nan': '', 'None': ''})
        part = part.drop_duplicates(subset=keys, keep='last').sort_values(tcol)
        part.to_csv(fn, index=False)
        n += len(part)
    return n


# --------------------------------------------------------------- streams --
def fetch_waves(api, start, end):
    """Waves + wind + partitions + surface temp, merged into one row per (time, source)."""
    common = dict(processingSources='all')
    waves = api.paged('waves', start, end, 500, includeWaves='true', **common)
    wind = api.paged('wind', start, end, 500, includeWaves='false', includeWindData='true', **common)
    parts = api.paged('partitionData', start, end, 500, includeWaves='false', includePartitionData='true', **common)
    sst = api.paged('surfaceTemp', start, end, 500, includeWaves='false', includeSurfaceTempData='true', **common)
    rows = {}

    def row(item):
        k = (epoch(item['timestamp']), item.get('processing_source') or 'embedded')
        if k not in rows:
            rows[k] = {'Epoch Time': k[0], 'Processing Source': k[1]}
        r = rows[k]
        if item.get('latitude') is not None:
            r['Latitude (deg)'] = item.get('latitude')
            r['Longitude (deg)'] = item.get('longitude')
        return r

    for w in waves:
        r = row(w)
        for a, b in WAVE_COLS.items():
            r[b] = w.get(a)
    for w in wind:
        r = row(w)
        r['Wind Speed (m/s)'] = w.get('speed')
        r['Wind Direction (deg)'] = w.get('direction')
    for p in parts:
        r = row(p)
        for i, part in enumerate((p.get('partitions') or [])[:2]):
            for a, b in PART_KEYS:
                r[f'Partition{i} {b}'] = part.get(a)
    for t in sst:
        row(t)['Surface Temperature (°C)'] = t.get('degrees')
    return pd.DataFrame(list(rows.values()))


def fetch_spectra(api, start, end):
    """Variance-density spectra (limit is 100 per call when spectra are included)."""
    items = api.paged('frequencyData', start, end, 100, includeWaves='false', includeFrequencyData='true',
                      processingSources='all')
    rows = []
    for it in items:
        f, S = it.get('frequency'), it.get('varianceDensity')
        if not f or not S:
            continue
        rows.append({'Epoch Time': epoch(it['timestamp']), 'Processing Source': it.get('processing_source') or 'embedded',
                     'f': ';'.join(str(x) for x in f), 'varianceDensity': ';'.join(str(x) for x in S)})
    return pd.DataFrame(rows)


def fetch_baro(api, start, end):
    items = api.paged('barometerData', start, end, 500, includeWaves='false', includeBarometerData='true',
                      processingSources='all')
    return pd.DataFrame([{'Epoch Time': epoch(b['timestamp']), 'Processing Source': b.get('processing_source') or 'baro',
                          'Mean Barometric Pressure (hPa)': b.get('value'),
                          'Latitude (deg)': b.get('latitude'), 'Longitude (deg)': b.get('longitude')} for b in items])


def fetch_status(api):
    j = api.get('latest-data', spotterId=bc.SPOTTER_ID)
    d = j.get('data') or {}
    t = None
    for key in ('waves', 'track'):
        arr = d.get(key) or []
        if arr:
            t = max(arr, key=lambda x: x['timestamp'])['timestamp']
            break
    return pd.DataFrame([{'Epoch Time': epoch(t or pd.Timestamp.now(tz='UTC')), 'Processing Source': 'status',
                          'Battery Voltage (V)': d.get('batteryVoltage'), 'Power (W)': d.get('batteryPower'),
                          'Humidity (%rel)': d.get('humidity'), 'Solar Voltage (V)': d.get('solarVoltage'),
                          'Comm Source': d.get('commSource')}])


def fetch_sensors(api, start, end):
    j = api.get('sensor-data', spotterId=bc.SPOTTER_ID, startDate=iso(start), endDate=iso(end))
    items = j.get('data') or []
    if isinstance(items, dict):   # be tolerant of a {data: {data: [...]}} shape
        items = items.get('data') or items.get('sensorData') or []
    keep = ['timestamp', 'sensorPosition', 'data_type_name', 'unit_type', 'units', 'value', 'latitude', 'longitude',
            'configuration_checksum']
    return pd.DataFrame([{k: it.get(k) for k in keep} for it in items])


# ------------------------------------------------------------------ main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-minutes', type=float, default=40, help='stop back-filling after this long (resumes next run)')
    a = ap.parse_args()
    token = os.environ.get('SOFAR_API_TOKEN', '').strip()
    if not token:
        raise SystemExit('❌ No SOFAR_API_TOKEN. Add it under Settings → Secrets and variables → Actions.')
    api = Sofar(token)
    st = load_state()
    now = pd.Timestamp.now(tz='UTC').floor('min')
    t_stop = time.time() + a.max_minutes * 60
    print(f'🛰️  {bc.SPOTTER_ID}: fetching new data up to {iso(now)}')

    def walk(stream, step_days, fn, save):
        start = ts(st[stream]) - OVERLAP
        total = 0
        while start < now and time.time() < t_stop:
            end = min(start + pd.Timedelta(days=step_days), now)
            df = fn(api, start, end)
            total += save(df) if len(df) else 0
            st[stream] = iso(end)
            save_state(st)                   # progress survives a timeout
            start = end
        print(f'   {stream:8s} → {total:6d} rows (now complete to {st[stream]})')

    try:
        walk('waves', 1, fetch_waves, lambda d: merge_daily(d, 'waves', ['Epoch Time', 'Processing Source']))
        walk('spectra', 0.5, fetch_spectra, lambda d: merge_daily(d, 'spectra', ['Epoch Time', 'Processing Source']))
        walk('baro', 1, fetch_baro, lambda d: merge_daily(d, 'baro', ['Epoch Time', 'Processing Source']))
        try:
            walk('sensors', 1, fetch_sensors,
                 lambda d: merge_daily(d, None, ['timestamp', 'sensorPosition', 'data_type_name'], 'sensors', 'sensors'))
        except requests.HTTPError as e:
            print(f'   sensors  → skipped ({e})')
    except (requests.RequestException, RuntimeError) as e:
        print(f'⚠️  Sofar is not responding properly right now ({e}). Progress is saved; the next hourly run picks up from here.')
    try:
        s = fetch_status(api)
        fn = os.path.join(DATA, 'live', f'{bc.SPOTTER_ID}_status.csv')
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        old = pd.read_csv(fn, dtype=str) if os.path.exists(fn) else pd.DataFrame()
        pd.concat([old, s.astype(str)]).drop_duplicates(subset=['Epoch Time'], keep='last').to_csv(fn, index=False)
        print(f"   status   → battery {s['Battery Voltage (V)'][0]} V, humidity {s['Humidity (%rel)'][0]}%")
    except requests.HTTPError as e:
        print(f'   status   → skipped ({e})')
    st['last_run'] = iso(pd.Timestamp.now(tz='UTC'))
    save_state(st)
    print(f'✅ done — {api.calls} API calls')


if __name__ == '__main__':
    main()
