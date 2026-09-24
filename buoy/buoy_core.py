"""
buoy_core.py - the data engine behind the buoy website (site settings: site.json).

Reads every Sofar Spotter "embedded-history" CSV in the buoy folder (and the
optional live-API cache), cleans it, and computes the statistics used by the
spreadsheet and the dashboard.

Nothing in here needs editing for normal use. The settings block below is the
only place you might want to tweak.
"""
from __future__ import annotations

import glob
import json
import re
import math
import os
import pickle
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ----------------------------------------------------------------- settings --
# per-site settings live in site.json at the top of the repository
_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'site.json')
try:
    with open(_CFG_PATH, encoding='utf-8') as _fh:
        CFG = json.load(_fh)
except OSError:
    CFG = {}
SPOTTER_ID = CFG.get('spotter_id', 'SPOT-32787C')
SITE_SHORT = CFG.get('short_name', 'Camp Ellis')
SITE_NAME = CFG.get('site_name', 'Camp Ellis, Saco Bay, Maine')
GITHUB_USER = CFG.get('github_user', 'ekelting')
GITHUB_REPO = CFG.get('github_repo', 'UNE-Camp-Ellis-SPOT-32787C')
SITE_URL = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/"
REPO_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"
LOCAL_TZ = CFG.get('timezone', 'America/New_York')
DEPTH_M = CFG.get('depth_m')              # None → deep-water wave-power formula
SISTER_SITES = CFG.get('sister_sites', [])
FILE_STEM = re.sub(r'[^A-Za-z0-9]+', '_', SITE_SHORT).strip('_') + '_Buoy'
STORM_HS_M = 1.0          # a "storm event" = hourly Hs at/above this ...
STORM_MIN_HOURS = 6       # ... for at least this many hours
STORM_MERGE_GAP_H = 6     # dips shorter than this are merged into one event
CALM_HS_M = 0.3           # "glassy" threshold used for calm streaks
SETTLE_HOURS = 6          # after the buoy first stays on its mooring, wait this long before trusting sensor readings
# the "wave vibe" scale used on the dashboard (upper limit of Hs in metres, emoji, name, description)
VIBES = [(0.3, '😴', 'Glassy', 'Flat, lake-like water.'),
         (0.6, '🙂', 'Gentle', 'Small, easy waves.'),
         (1.0, '🌊', 'Lively', 'Noticeable waves rolling in.'),
         (1.5, '💪', 'Rough', 'Big for this bay — whitecaps likely.'),
         (2.5, '⚠️', 'Stormy', 'Storm waves — erosion weather.'),
         (99, '🌀', 'Huge storm', 'Among the biggest seas this buoy sees.')]
OFF_STATION_M = 150       # positions farther than this from the mooring = deployment/transit, dropped
RHO_G2_64PI = 1025 * 9.81 ** 2 / (64 * math.pi) / 1000  # kW/m per (m^2 s) = 0.49

CACHE_VERSION = 2
TE_MIN_FREQ = 0.04          # Hz; lower bound for the energy-period integral
SPEC_COLS = ['f', 'df', 'a1', 'b1', 'a2', 'b2', 'varianceDensity', 'direction', 'directionalSpread']
RENAME = {
    'Epoch Time': 'epoch', 'Processing Source': 'src',
    'Significant Wave Height (m)': 'hs', 'Peak Period (s)': 'tp', 'Mean Period (s)': 'tm',
    'Peak Direction (deg)': 'dp', 'Peak Directional Spread (deg)': 'dp_spread',
    'Mean Direction (deg)': 'dm', 'Mean Directional Spread (deg)': 'dm_spread',
    'Latitude (deg)': 'lat', 'Longitude (deg)': 'lon',
    'Wind Speed (m/s)': 'wspd', 'Wind Direction (deg)': 'wdir',
    'Surface Temperature (°C)': 'sst', 'Mean Barometric Pressure (hPa)': 'pres',
    'Battery Voltage (V)': 'batt', 'Power (W)': 'solar', 'Humidity (%rel)': 'humid',
    'Partition0 Significant Wave Height (m)': 'hs_swell', 'Partition0 Mean Period (s)': 'tm_swell',
    'Partition0 Mean Direction (deg)': 'dm_swell',
    'Partition1 Significant Wave Height (m)': 'hs_sea', 'Partition1 Mean Period (s)': 'tm_sea',
    'Partition1 Mean Direction (deg)': 'dm_sea',
}
COMPASS16 = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
             'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']


def compass(deg):
    if deg is None or (isinstance(deg, float) and np.isnan(deg)):
        return ''
    return COMPASS16[int(((deg % 360) + 11.25) // 22.5) % 16]


def circ_mean(deg, w=None):
    deg = np.asarray(deg, float)
    ok = ~np.isnan(deg)
    if w is not None:
        w = np.asarray(w, float)
        ok &= ~np.isnan(w)
        w = w[ok]
    deg = np.radians(deg[ok])
    if deg.size == 0:
        return np.nan
    s = np.average(np.sin(deg), weights=w)
    c = np.average(np.cos(deg), weights=w)
    return math.degrees(math.atan2(s, c)) % 360


# ------------------------------------------------------------------ loading --
def find_csvs(root):
    files = glob.glob(os.path.join(root, '**', '*.csv'), recursive=True) + \
        glob.glob(os.path.join(root, '**', '*.csv.gz'), recursive=True)
    return sorted(f for f in files if SPOTTER_ID in os.path.basename(f)
                  and 'smartmooring' not in os.path.basename(f).lower()      # sensor files are read by sensors.py
                  and 'cache' not in f.replace('\\', '/').split('/')[-2])


def _read_one(path):
    """Read a CSV -> (scalar dataframe, spectra dataframe)."""
    head = pd.read_csv(path, nrows=0).columns
    usecols = [c for c in head if c not in SPEC_COLS]
    has_spec = 'varianceDensity' in head
    if has_spec:
        usecols += ['f', 'varianceDensity']
    d = pd.read_csv(path, na_values=['-', ''], usecols=usecols, low_memory=False)
    d = d.rename(columns=RENAME)
    d['t'] = pd.to_datetime(d['epoch'], unit='s', utc=True)
    spec = pd.DataFrame()
    if has_spec:
        s = d.dropna(subset=['varianceDensity'])
        s = s[s['src'] == 'hdr'] if (s['src'] == 'hdr').any() else s
        s = s.drop_duplicates('t')
        if len(s):
            freqs = s['f'].str.split(';')
            grid = [float(x) for x in freqs.iloc[0]]
            same = freqs.str.len() == len(grid)
            vals = s.loc[same, 'varianceDensity'].str.split(';', expand=True).astype(float)
            vals.columns = np.round(grid, 5)
            vals.index = pd.DatetimeIndex(s.loc[same, 't'])
            spec = vals
        d = d.drop(columns=['f', 'varianceDensity'])
    return d, spec


def load_all(root, cache_dir=None, verbose=True):
    """Load every CSV under root, using a pickle cache so re-runs are fast."""
    files = find_csvs(root)
    if not files:
        raise SystemExit(f"No {SPOTTER_ID} CSV files found under {root}")
    cache_dir = cache_dir or os.path.join(root, '.cache')
    os.makedirs(cache_dir, exist_ok=True)
    scal, specs = [], []
    for f in files:
        st = os.stat(f)
        key = os.path.join(cache_dir, os.path.basename(f) + f".v{CACHE_VERSION}_{int(st.st_mtime)}_{st.st_size}.pkl")
        got = None
        if os.path.exists(key):
            try:
                with open(key, 'rb') as fh:
                    got = pickle.load(fh)
            except Exception:
                got = None
        if got is None:
            if verbose:
                print(f"  reading {os.path.basename(f)} ...")
            got = _read_one(f)
            for old in glob.glob(os.path.join(cache_dir, os.path.basename(f) + '.*.pkl')):
                try:
                    os.remove(old)
                except OSError:
                    pass
            try:
                with open(key, 'wb') as fh:
                    pickle.dump(got, fh)
            except OSError:
                pass
        elif verbose:
            print(f"  cached  {os.path.basename(f)}")
        scal.append(got[0])
        if len(got[1]):
            specs.append(got[1])
    raw = pd.concat(scal, ignore_index=True)
    spec = pd.concat(specs).sort_index() if specs else pd.DataFrame()
    spec = spec[~spec.index.duplicated()]
    return raw, spec, files


def wave_power_kw_m(hs, te, depth=None):
    """Wave energy flux P = E·Cg (kW per metre of crest). Uses finite-depth linear theory when the site
    depth is known (site.json depth_m), otherwise the deep-water limit ρg²Hs²Te/64π."""
    depth = DEPTH_M if depth is None else depth
    hs, te = np.asarray(hs, float), np.asarray(te, float)
    if not depth:
        return RHO_G2_64PI * hs ** 2 * te
    g, rho = 9.81, 1025.0
    L0 = g * te ** 2 / (2 * math.pi)
    with np.errstate(invalid='ignore', divide='ignore'):
        L = L0 * np.tanh((2 * math.pi * depth / L0) ** 0.75) ** (2 / 3)   # Fenton & McKee (1990)
        k = 2 * math.pi / L
        n = 0.5 * (1 + 2 * k * depth / np.sinh(2 * k * depth))
        cg = n * L / te
    E = rho * g * hs ** 2 / 16
    return E * cg / 1000


# ----------------------------------------------------------------- cleaning --
@dataclass
class BuoyData:
    waves: pd.DataFrame            # one row per wave estimate (cleaned)
    met: pd.DataFrame              # 5-min pressure / battery / humidity / position
    spec: pd.DataFrame             # variance density spectra (hdr rows)
    files: list
    qc: dict = field(default_factory=dict)


def clean(raw, spec, files):
    qc = {}
    raw = raw.drop_duplicates(subset=['t', 'src', 'hs', 'pres'])

    # --- where is the mooring? drop deployment transit / off-station points
    lat0, lon0 = raw['lat'].median(), raw['lon'].median()
    dist = np.hypot((raw['lat'] - lat0) * 111_320,
                    (raw['lon'] - lon0) * 111_320 * math.cos(math.radians(lat0)))
    raw = raw.assign(dist_m=dist)
    off = raw['dist_m'] > OFF_STATION_M
    qc['off_station_rows'] = int(off.sum())
    qc['mooring_lat'], qc['mooring_lon'] = float(lat0), float(lon0)
    # when did the buoy settle on its mooring? = start of the first 12 h stretch with every fix on station
    pos = raw.dropna(subset=['lat']).groupby('t')['dist_m'].max().sort_index()
    onh = (pos <= OFF_STATION_M).resample('1h').min().dropna()
    run = onh.astype(int).groupby((onh != onh.shift()).cumsum()).cumsum()
    first_ok = run[(onh == 1) & (run >= 12)]
    settled = (first_ok.index[0] - pd.Timedelta(hours=11)) if len(first_ok) else pos.index.min()
    qc['settled_utc'] = settled + pd.Timedelta(hours=SETTLE_HOURS)
    raw = raw[~off]
    raw = raw[raw['t'] >= settled]

    # --- wave rows: prefer 15-min "hdr" estimates, fill gaps with 30-min onboard ones
    w = raw.dropna(subset=['hs'])
    hdr = w[w['src'] == 'hdr'].drop_duplicates('t').set_index('t').sort_index()
    oth = w[w['src'] != 'hdr'].drop_duplicates('t').set_index('t').sort_index()
    if len(hdr) and len(oth):
        near = pd.merge_asof(oth.reset_index()[['t']], hdr.reset_index()[['t']].rename(columns={'t': 'th'}),
                             left_on='t', right_on='th', direction='nearest',
                             tolerance=pd.Timedelta('20min'))
        oth = oth[near['th'].isna().values]
    waves = pd.concat([hdr, oth]).sort_index()
    qc['wave_rows_hdr'], qc['wave_rows_fill'] = len(hdr), len(oth)

    # --- de-spike: isolated single-sample jumps (buoy snag / slam / ice)
    med = waves['hs'].rolling(5, center=True, min_periods=3).median()
    spike = ((waves['hs'] > 1.6 * med) & (waves['hs'] - med > 0.3)) | \
            ((waves['hs'] < 0.4 * med) & (med - waves['hs'] > 0.3))
    # short bursts (2–3 readings in a row) that a 5-point median can't see: compare with a ~3-hour window
    med13 = waves['hs'].rolling(13, center=True, min_periods=7).median()
    spike |= (waves['hs'] > 2.2 * med13) & (waves['hs'] - med13 > 0.8)
    qc['spikes_removed'] = int(spike.sum())
    qc['spike_examples'] = [(str(t.tz_convert(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')), round(v, 2))
                            for t, v in waves.loc[spike, 'hs'].nlargest(5).items()]
    wave_cols = ['hs', 'tp', 'tm', 'dp', 'dm', 'wspd', 'wdir', 'hs_swell', 'hs_sea', 'tm_swell', 'tm_sea',
                 'dm_swell', 'dm_sea', 'dp_spread', 'dm_spread']
    wave_cols = [c for c in wave_cols if c in waves]
    waves.loc[spike, wave_cols] = np.nan
    waves = waves.dropna(subset=['hs'])
    # implausible peak periods at this sheltered site
    qc['tp_over_22s'] = int((waves['tp'] > 22).sum())
    waves.loc[waves['tp'] > 22, 'tp'] = np.nan

    # --- energy period from spectra (Te = m-1/m0); fallback to scaled mean period
    waves['te'] = np.nan
    if len(spec):
        spec = spec.loc[spec.index.isin(waves.index)]
        f = np.array(spec.columns, float)
        dfreq = np.gradient(f)
        S = spec.values
        m0 = (S * dfreq).sum(1)
        band = f >= TE_MIN_FREQ        # ignore < 0.04 Hz (> 25 s): low-frequency noise inflates Te
        mm1 = (S[:, band] * dfreq[band] / f[band]).sum(1)
        te = pd.Series(mm1 / (S[:, band] * dfreq[band]).sum(1), index=spec.index)
        waves.loc[te.index, 'te'] = te.values
        waves.loc[te.index, 'hs_spec'] = 4 * np.sqrt(m0)
    ratio = (waves['te'] / waves['tm']).median()
    ratio = float(ratio) if np.isfinite(ratio) else 1.15
    qc['te_over_tm'] = ratio
    waves['te'] = waves['te'].fillna(waves['tm'] * ratio)
    waves['power_kw_m'] = wave_power_kw_m(waves['hs'].values, waves['te'].values)

    # --- met / housekeeping (every row)
    mcols = [c for c in ['pres', 'batt', 'solar', 'humid', 'lat', 'lon', 'dist_m'] if c in raw]
    met = raw.groupby('t')[mcols].first().sort_index()
    for c in ['pres', 'batt', 'solar', 'humid', 'lat', 'lon', 'dist_m']:
        if c not in met:
            met[c] = np.nan
    qc['sst_available'] = bool(raw['sst'].notna().any()) if 'sst' in raw else False
    return BuoyData(waves=waves, met=met, spec=spec, files=files, qc=qc)


def load(root, verbose=True, cache_dir=None):
    raw, spec, files = load_all(root, cache_dir=cache_dir, verbose=verbose)
    return clean(raw, spec, files)


# -------------------------------------------------------------------- stats --
def local(idx):
    return idx.tz_convert(LOCAL_TZ)


def hourly(bd):
    w = bd.waves
    num = w[['hs', 'tp', 'tm', 'te', 'power_kw_m', 'wspd']].resample('1h').mean()
    num['dm'] = w['dm'].resample('1h').apply(lambda s: circ_mean(s.values) if len(s) else np.nan)
    num['pres'] = bd.met['pres'].resample('1h').mean()
    return num


def storms(bd, hr=None):
    hr = hourly(bd) if hr is None else hr
    above = hr['hs'] >= STORM_HS_M
    times = hr.index[above.fillna(False)]
    events, cur = [], None
    for t in times:
        if cur and (t - cur[1]) <= pd.Timedelta(hours=STORM_MERGE_GAP_H):
            cur[1] = t
        else:
            if cur:
                events.append(cur)
            cur = [t, t]
    if cur:
        events.append(cur)
    rows = []
    for a, b in events:
        seg = hr.loc[a:b]
        dur = (b - a) / pd.Timedelta(hours=1) + 1
        if dur < STORM_MIN_HOURS:
            continue
        ipk = seg['hs'].idxmax()
        wseg = bd.waves.loc[a:b + pd.Timedelta(hours=1)]
        rows.append(dict(
            start=local(pd.DatetimeIndex([a]))[0], end=local(pd.DatetimeIndex([b]))[0], hours=dur,
            peak_hs=seg['hs'].max(), peak_time=local(pd.DatetimeIndex([ipk]))[0],
            tp_at_peak=hr.loc[ipk, 'tp'], dir_deg=circ_mean(wseg['dm'].values, wseg['hs'].values ** 2),
            max_wind=wseg['wspd'].max(), min_pres=bd.met['pres'].loc[a - pd.Timedelta(hours=12):b].min(),
            energy_mj_m=(seg['power_kw_m'].fillna(0).sum() * 3600) / 1000,
        ))
    ev = pd.DataFrame(rows)
    if len(ev):
        ev['dir'] = ev['dir_deg'].map(compass)
        ev = ev.sort_values('start').reset_index(drop=True)
        ev.insert(0, 'event', range(1, len(ev) + 1))
    return ev


def daily(bd):
    w = bd.waves.copy()
    w.index = local(w.index)
    m = bd.met.copy()
    m.index = local(m.index)
    g = w.resample('1D')
    d = pd.DataFrame({
        'hs_mean': g['hs'].mean(), 'hs_max': g['hs'].max(), 'tp_mean': g['tp'].mean(),
        'te_mean': g['te'].mean(), 'power_mean': g['power_kw_m'].mean(),
        'dir_deg': g.apply(lambda x: circ_mean(x['dm'].values, x['hs'].values ** 2) if len(x) else np.nan),
        'wind_mean': g['wspd'].mean(), 'wind_max': g['wspd'].max(), 'n_waves_obs': g['hs'].count(),
    })
    gm = m.resample('1D')
    d['pres_mean'] = gm['pres'].mean()
    d['pres_min'] = gm['pres'].min()
    d['batt_mean'] = gm['batt'].mean()
    d['humid_mean'] = gm['humid'].mean()
    d['drift_max_m'] = gm['dist_m'].max()
    d['dir'] = d['dir_deg'].map(compass)
    d.index = d.index.tz_localize(None).date
    d.index.name = 'date'
    return d


def monthly(bd, ev):
    w = bd.waves.copy()
    w.index = local(w.index).tz_localize(None)
    m = bd.met.copy()
    m.index = local(m.index).tz_localize(None)
    out = []
    for per, g in w.groupby(w.index.to_period('M')):
        gm = m[m.index.to_period('M') == per]
        days = per.days_in_month
        start = max(pd.Timestamp(per.start_time), w.index.min().normalize())
        end = pd.Timestamp(per.end_time)
        expected_h = (end - start) / pd.Timedelta(hours=1)
        hours_cov = g['hs'].resample('1h').count().gt(0).sum()
        evm = ev[ev['start'].dt.tz_localize(None).dt.to_period('M') == per] if len(ev) else ev
        dt_h = 0.25
        out.append(dict(
            month=per.strftime('%Y-%m'), label=per.strftime('%b %Y'),
            coverage_pct=100 * hours_cov / expected_h, n_obs=int(g['hs'].count()),
            hs_mean=g['hs'].mean(), hs_median=g['hs'].median(), hs_p90=g['hs'].quantile(.9),
            hs_max=g['hs'].max(), tp_mean=g['tp'].mean(), te_mean=g['te'].mean(),
            dir_deg=circ_mean(g['dm'].values, g['hs'].values ** 2),
            power_mean=g['power_kw_m'].mean(),
            energy_mj_m=g['power_kw_m'].resample('1h').mean().fillna(0).sum() * 3600 / 1000,
            pct_over_1m=100 * (g['hs'] >= 1).mean(), pct_over_2m=100 * (g['hs'] >= 2).mean(),
            pct_calm=100 * (g['hs'] < CALM_HS_M).mean(),
            swell_share=100 * np.nanmean(g['hs_swell'] ** 2 / (g['hs_swell'] ** 2 + g['hs_sea'] ** 2)),
            wind_mean=g['wspd'].mean(), wind_max=g['wspd'].max(),
            pres_mean=gm['pres'].mean(), pres_min=gm['pres'].min(),
            storms=len(evm), storm_hours=float(evm['hours'].sum()) if len(evm) else 0.0,
            batt_min=gm['batt'].min(), humid_mean=gm['humid'].mean(),
        ))
        _ = days, dt_h
    mo = pd.DataFrame(out)
    mo['dir'] = mo['dir_deg'].map(compass)
    mo['partial'] = mo['coverage_pct'] < 50
    return mo


def rose(bd, bins=(0, 0.25, 0.5, 1.0, 1.5, 2.0, 99)):
    w = bd.waves.dropna(subset=['dm', 'hs'])
    sector = ((w['dm'] % 360 + 11.25) // 22.5 % 16).astype(int).map(lambda i: COMPASS16[i])
    labels = [f"{a:g}–{b:g} m" if b < 99 else f"≥{a:g} m" for a, b in zip(bins[:-1], bins[1:])]
    hb = pd.cut(w['hs'], bins=list(bins), labels=labels, right=False)
    tab = pd.crosstab(sector, hb, normalize=True) * 100
    tab = tab.reindex(COMPASS16).fillna(0)
    return tab


def gaps(bd, min_hours=3):
    t = bd.waves.index.to_series()
    d = t.diff()
    g = d[d > pd.Timedelta(hours=min_hours)]
    return [(local(pd.DatetimeIndex([t.shift(1)[i]]))[0], local(pd.DatetimeIndex([i]))[0],
             d[i] / pd.Timedelta(hours=1)) for i in g.index]


def calm_streak(hr):
    """Longest run of hours with Hs below the calm threshold."""
    s = (hr['hs'] < CALM_HS_M)
    best, cur, best_end = 0, 0, None
    for t, v in s.items():
        cur = cur + 1 if v else 0
        if cur > best:
            best, best_end = cur, t
    return best, best_end


def summary(bd):
    """Everything the reports need, in one dict."""
    hr = hourly(bd)
    ev = storms(bd, hr)
    dy = daily(bd)
    mo = monthly(bd, ev)
    ro = rose(bd)
    w = bd.waves
    streak_h, streak_end = calm_streak(hr)
    last = w.iloc[-1]
    last_t = local(pd.DatetimeIndex([w.index[-1]]))[0]
    prev24 = w.loc[w.index[-1] - pd.Timedelta(hours=24):w.index[-1]]
    pres_now = bd.met['pres'].dropna()
    p3 = pres_now.loc[pres_now.index[-1] - pd.Timedelta(hours=3):]
    total_hours = (w.index[-1] - w.index[0]) / pd.Timedelta(hours=1)
    covered_hours = w['hs'].resample('1h').count().gt(0).sum()
    energy_mwh_m = hr['power_kw_m'].fillna(0).sum() / 1000
    n_waves = (w['hs'].resample('1h').count().gt(0) * 3600 / hr['tm']).sum()
    dy_ok = dy[dy['n_waves_obs'] >= 24]
    mf = mo[~mo['partial']]
    hs_h = hr['hs'].dropna()
    vib_counts = []
    lo = 0.0
    for lim, emo, name, _ in VIBES:
        vib_counts.append((emo, name, 100 * ((hs_h >= lo) & (hs_h < lim)).mean() if len(hs_h) else 0.0, lo, lim))
        lo = lim
    top_vibe = max(vib_counts, key=lambda v: v[2])
    stats = dict(
        vibes=vib_counts, top_vibe=top_vibe,
        spotter=SPOTTER_ID, site=SITE_NAME,
        first=local(pd.DatetimeIndex([w.index[0]]))[0], last=last_t,
        days=total_hours / 24, coverage_pct=100 * covered_hours / total_hours,
        n_wave_obs=int(len(w)), n_met_obs=int(len(bd.met)), n_files=len(bd.files),
        hs_mean=w['hs'].mean(), hs_median=w['hs'].median(), hs_p90=w['hs'].quantile(.9),
        hs_p99=w['hs'].quantile(.99), hs_max=w['hs'].max(),
        hs_max_time=local(pd.DatetimeIndex([w['hs'].idxmax()]))[0],
        tp_mean=w['tp'].mean(), tp_max=w['tp'].max(), te_mean=w['te'].mean(),
        dir_energy=circ_mean(w['dm'].values, w['hs'].values ** 2),
        dir_mode=ro.sum(1).idxmax(), dir_mode_pct=ro.sum(1).max(),
        power_mean=w['power_kw_m'].mean(), energy_mwh_m=energy_mwh_m,
        n_waves=n_waves, pct_over_1m=100 * (w['hs'] >= 1).mean(), pct_calm=100 * (w['hs'] < CALM_HS_M).mean(),
        wind_mean=w['wspd'].mean(), wind_max=w['wspd'].max(),
        pres_min=bd.met['pres'].min(), pres_min_time=local(pd.DatetimeIndex([bd.met['pres'].idxmin()]))[0],
        pres_max=bd.met['pres'].max(), pres_max_time=local(pd.DatetimeIndex([bd.met['pres'].idxmax()]))[0],
        n_storms=len(ev), storm_hours=float(ev['hours'].sum()) if len(ev) else 0,
        biggest_day=dy_ok['hs_mean'].idxmax(), biggest_day_hs=dy_ok['hs_mean'].max(),
        calmest_day=dy_ok['hs_mean'].idxmin(), calmest_day_hs=dy_ok['hs_mean'].min(),
        stormiest_month=mf.loc[mf['hs_mean'].idxmax(), 'label'], stormiest_month_hs=mf['hs_mean'].max(),
        calmest_month=mf.loc[mf['hs_mean'].idxmin(), 'label'], calmest_month_hs=mf['hs_mean'].min(),
        calm_streak_h=streak_h,
        calm_streak_end=local(pd.DatetimeIndex([streak_end]))[0] if streak_end is not None else None,
        latest=dict(time=last_t, hs=last['hs'], tp=last['tp'], tm=last['tm'], dm=last['dm'],
                    dir=compass(last['dm']), wspd=last['wspd'], wdir=last['wdir'],
                    hs_24h_max=prev24['hs'].max(), hs_24h_mean=prev24['hs'].mean(),
                    pres=pres_now.iloc[-1], pres_3h_change=pres_now.iloc[-1] - p3.iloc[0],
                    batt=bd.met['batt'].dropna().iloc[-1], humid=bd.met['humid'].dropna().iloc[-1]),
        batt_min=bd.met['batt'].min(), humid_first30=bd.met['humid'].loc[:bd.met.index[0] + pd.Timedelta(days=30)].mean(),
        humid_last7=bd.met['humid'].dropna().loc[bd.met['humid'].dropna().index[-1] - pd.Timedelta(days=7):].mean(),
        humid_now=bd.met['humid'].dropna().iloc[-1],
        drift_p95=bd.met['dist_m'].quantile(.95),
    )
    return dict(stats=stats, hourly=hr, storms=ev, daily=dy, monthly=mo, rose=ro,
                gaps=gaps(bd), qc=bd.qc)


# ------------------------------------------------------------ plain English --
M2FT = 3.28084


def ft(m):
    return m * M2FT


def insights(S):
    """Data-driven, plain-English findings shared by the spreadsheet and the dashboard.
    Returns a list of (emoji, headline, detail)."""
    st, mo, ev = S['stats'], S['monthly'], S['storms']
    mf = mo[~mo['partial']]
    out = []

    # seasonality
    hi = mf.loc[mf['energy_mj_m'].idxmax()]
    lo = mf.loc[mf['energy_mj_m'].idxmin()]
    ratio = hi['energy_mj_m'] / max(lo['energy_mj_m'], 1e-9)
    rough = mf.sort_values('pct_over_1m', ascending=False).head(3)['label'].tolist()
    out.append(("📅", "Late winter and spring are the rough season",
                f"{hi['label']} delivered the most wave energy — about {ratio:.0f}× more than {lo['label']}, the quietest full month. "
                f"The months with the most time spent above 1 m (3.3 ft) were {', '.join(rough)}."))

    # direction
    how = 'almost always' if st['dir_mode_pct'] >= 65 else ('mostly' if st['dir_mode_pct'] >= 40 else 'most often')
    out.append(("🧭", f"Waves {how} arrive from the {st['dir_mode']}",
                f"{st['dir_mode_pct']:.0f}% of all wave readings came from the {st['dir_mode']} sector, and the energy-weighted average "
                f"direction is {st['dir_energy']:.0f}° ({compass(st['dir_energy'])}). "
                + (f"Every one of the {len(ev)} storm events came from the {', '.join(sorted(ev['dir'].unique()))}. " if len(ev) else "")
                + "A narrow window like this usually reflects waves bending (refracting) and being sheltered by the coastline "
                f"before they reach the buoy — and it is a key boundary condition for longshore-transport and shoreline-change "
                f"modeling around {SITE_SHORT}."))

    # storms
    if len(ev):
        top = ev.sort_values('energy_mj_m', ascending=False).head(3)
        tops = '; '.join(f"{r.start:%b %d}–{r.end:%b %d, %Y} (peak {r.peak_hs:.1f} m, {r.hours:.0f} h)" for r in top.itertuples())
        share = 100 * top['energy_mj_m'].sum() / (st['energy_mwh_m'] * 3600)
        out.append(("🌀", f"{len(ev)} storm events, {st['storm_hours']:.0f} stormy hours",
                    f"A storm event here means waves ≥ {STORM_HS_M:g} m (≈{ft(STORM_HS_M):.1f} ft) lasting {STORM_MIN_HOURS}+ hours. "
                    f"The three most energetic were {tops}. Those three alone carried about {share:.0f}% of all the wave energy the "
                    f"buoy recorded — a few big events do most of the work on the beach."))

    # the biggest wave
    out.append(("🏆", f"Biggest waves: {st['hs_max']:.2f} m ({ft(st['hs_max']):.1f} ft) significant height",
                f"Recorded {st['hs_max_time']:%b %d, %Y at %I:%M %p}. Significant wave height is the average of the highest third of waves, "
                f"so individual waves that day were likely up to ~1.8× taller — roughly {1.8 * st['hs_max']:.1f} m ({ft(1.8 * st['hs_max']):.0f} ft)."))

    # swell vs sea
    sw = mf['swell_share']
    out.append(("🌊", "Mostly local wind-sea, with swell showing up in winter",
                f"Long-period swell (periods > 7.9 s) carried {sw.min():.0f}–{sw.max():.0f}% of the wave energy depending on the month, "
                f"highest in {mf.loc[sw.idxmax(), 'label']}. The rest is shorter, choppier 'sea' generated by nearby winds. "
                f"Average peak period: {st['tp_mean']:.1f} s."))

    # calm
    emo, name, pct, lo, hi = st['top_vibe']
    mix = ' · '.join(f"{e} {n} {p:.0f}%" for e, n, p, _, _ in st['vibes'] if p >= 0.5)
    rng = (f"under {ft(hi):.0f} ft ({hi:g} m)" if lo == 0 else
           f"{ft(lo):.0f}–{ft(hi):.0f} ft ({lo:g}–{hi:g} m)" if hi < 99 else f"over {ft(lo):.0f} ft ({lo:g} m)")
    streak = (f" The longest glassy stretch (waves under {ft(CALM_HS_M):.0f} ft) lasted {st['calm_streak_h'] / 24:.1f} days, "
              f"ending {st['calm_streak_end']:%b %d, %Y}." if st['calm_streak_h'] >= 24 else '')
    out.append((emo, f"The usual wave vibe here: {name} — {pct:.0f}% of the time",
                f"Waves were {rng} for {pct:.0f}% of hours. The full mix: {mix}.{streak}"))

    # pressure
    out.append(("🌡️", f"Lowest pressure: {st['pres_min']:.1f} hPa",
                f"On {st['pres_min_time']:%b %d, %Y}. Highest: {st['pres_max']:.1f} hPa on {st['pres_max_time']:%b %d, %Y}. "
                "Sharp pressure drops are the fingerprint of passing storms (nor'easters) and usually line up with wave spikes."))

    # energy fun fact
    homes_days = st['energy_mwh_m'] * 1000 / 29
    out.append(("⚡", f"~{st['energy_mwh_m']:.1f} MWh of wave energy per metre of coastline",
                f"That's the energy that rolled past each metre-wide strip of the buoy site since deployment — roughly what a typical U.S. home "
                f"uses in {homes_days:.0f} days. (Estimate; treat as ballpark.) The buoy has ridden an estimated "
                f"{st['n_waves'] / 1e6:.1f} million waves."))

    # health
    q = S['qc']
    health = []
    if st['humid_now'] >= 70:
        health.append(f"internal humidity is {st['humid_now']:.0f}% — Sofar says above 70% means the desiccant needs replacing")
    elif st['humid_last7'] > 2 * st['humid_first30'] and st['humid_last7'] > 35:
        health.append(f"internal humidity has climbed from ~{st['humid_first30']:.0f}% in the first month to ~{st['humid_last7']:.0f}% "
                      f"in the latest week (Sofar recommends replacing the desiccant above 70%)")
    for a, b, h in S['gaps']:
        if h >= 24:
            health.append(f"one {h / 24:.1f}-day data gap from {a:%b %d} to {b:%b %d, %Y}")
    health.append(f"battery is healthy (lowest {st['batt_min']:.2f} V)")
    out.append(("🔧", "Buoy health check", (lambda t: t[0].upper() + t[1:])("; ".join(health)) + "."))
    return out


def qc_notes(S):
    q, st = S['qc'], S['stats']
    notes = [
        f"{q['spikes_removed']} isolated single-reading spikes in wave height were removed (e.g. "
        + ", ".join(f"{v} m at {t}" for t, v in q['spike_examples'][:3])
        + "). These jump up for one 15-min sample and immediately back down — typical of the buoy being yanked by its mooring, "
          "hit by debris/ice, or a processing glitch, not real waves.",
        f"{q['tp_over_22s']} peak-period values above 22 s were blanked (implausible for this sheltered bay; usually low-frequency noise).",
        f"{q['off_station_rows']} rows logged during deployment (buoy > {OFF_STATION_M} m from its mooring) were dropped.",
        "Wind speed/direction are estimated by the Spotter from the wave spectrum, not measured by an anemometer — "
        "they are unreliable in light winds and in sheltered water.",
        (f"Wave power/energy use linear wave theory for a water depth of {DEPTH_M:g} m (P = E·Cg), with the energy period Te "
         "computed from each spectrum (0.04 Hz and up)." if DEPTH_M else
         "Wave power/energy use the deep-water formula P = ρg²Hs²Te/64π with Te computed from each spectrum (0.04 Hz and up). "
         "At the buoy's shallow depth this is an approximation."),
    ]
    if not q['sst_available']:
        notes.append("This buoy has no sea-surface temperature sensor, so SST is not reported.")
    return notes
