"""
build.py — turns everything under data/ into the website in site/:
  site/index.html                         the dashboard
  site/<Site>_Buoy_Summary.xlsx           the spreadsheet (opens in Excel / Google Sheets)
  site/data/*.csv                         tidy tables (hourly, daily, monthly, storms, sensors)
                                          — link these into Google Sheets with =IMPORTDATA(url)

Usage:  python buoy/build.py [--data data] [--out site]
"""
import argparse
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import buoy_core as bc  # noqa: E402
import make_dashboard as mdash  # noqa: E402
import make_spreadsheet as msheet  # noqa: E402
import sensors as sn  # noqa: E402

ROOT = os.path.dirname(HERE)


def fetch_tides():
    """NOAA high/low tide predictions (yesterday → +3 days, local time, feet above MLLW). Empty list if NOAA is unreachable."""
    import json
    import urllib.request
    st = bc.CFG.get('tide_station')
    if not st:
        return []
    d0 = (pd.Timestamp.now(tz=bc.LOCAL_TZ) - pd.Timedelta(days=1)).strftime('%Y%m%d')
    url = ('https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions&application=UNE_buoy'
           f'&begin_date={d0}&range=120&datum=MLLW&station={st}&time_zone=lst_ldt&units=english&interval=hilo&format=json')
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            pr = json.load(r).get('predictions') or []
        print(f'🌙 {len(pr)} tide highs/lows from NOAA station {st}')
        return [{'t': x['t'], 'v': round(float(x['v']), 2), 'type': x['type']} for x in pr]
    except Exception as e:  # noqa: BLE001 — tides are a nice-to-have; never break the build
        print(f'::warning::Could not get tide predictions from NOAA ({e}); the page will try again in the browser.')
        return []


def export_latest(S, SS, path, days=14):
    """Small JSON the UNE Marine Monitoring Network overview page reads: latest values + last 2 weeks hourly."""
    import json
    import numpy as np
    num = lambda v: None if v is None or not np.isfinite(float(v)) else round(float(v), 3)
    st, lt, H = S['stats'], S['stats']['latest'], S['hourly']
    t0 = H.index.max() - pd.Timedelta(days=days)
    H = H[H.index > t0]
    now = dict(hs_m=num(lt['hs']), tp_s=num(lt['tp']), wave_dir=lt['dir'], wave_dir_deg=num(lt['dm']),
               wind_ms=num(lt.get('wspd')), wind_dir_deg=num(lt.get('wdir')), pres_hpa=num(lt['pres']))
    rec = {'t': [t.strftime('%Y-%m-%dT%H:%MZ') for t in H.index], 'hs_m': [num(v) for v in H['hs'].values]}
    if SS and SS.get('has'):
        SH = SS['hourly']
        for grp, key, k in [('temp', 'water_temp_c', 1.0), ('do_conc', 'do_mgl', None), ('do_sat', 'do_pct', 1.0)]:
            ser = [x for x in SS['series'] if x['group'] == grp]
            if not ser:
                continue
            s0 = ser[0]
            kk = k if k is not None else (sn.UMOL_TO_MGL if 'mol' in s0['units'].lower() else 1.0)
            now[key] = num(s0['latest'] * kk)
            col = SH[s0['col']].reindex(H.index) * kk
            rec[key] = [num(v) for v in col.values]
    js = dict(id=bc.GITHUB_REPO, kind='wave', name=bc.SITE_SHORT, device=st['spotter'], model='Sofar Spotter',
              lat=bc.CFG.get('mooring_lat'), lon=bc.CFG.get('mooring_lon'), url=bc.SITE_URL,
              last_obs=pd.Timestamp(lt['time']).tz_convert('UTC').strftime('%Y-%m-%dT%H:%M:%SZ'),
              generated=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%dT%H:%M:%SZ'),
              now=now, recent=rec, insights=[[e, h] for e, h, _ in bc.insights(S)][:4])
    with open(path, 'w') as fh:
        json.dump(js, fh, ensure_ascii=False, separators=(',', ':'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=os.path.join(ROOT, 'data'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'site'))
    ap.add_argument('--inline-plotly', action='store_true', help='embed plotly.js (works offline, +4.5 MB)')
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(os.path.join(a.out, 'data'), exist_ok=True)

    print('📥 reading wave data ...')
    bd = bc.load(a.data, verbose=False, cache_dir=os.path.join(ROOT, '.cache'))
    S = bc.summary(bd)
    print('🧪 reading Smart Mooring sensor data ...')
    SS = sn.build(sn.load(a.data, since=bd.qc.get('settled_utc')))
    print(f"   {len(SS.get('series', []))} sensor channels" if SS.get('has') else '   none yet')

    xlsx = f'{bc.FILE_STEM}_Summary.xlsx'
    msheet.build_xlsx(S, os.path.join(a.out, xlsx), SS)

    # tidy CSV exports (handy for Google Sheets IMPORTDATA / MATLAB / R)
    L = lambda df: df.set_axis(bc.local(df.index).tz_localize(None)) if isinstance(df.index, pd.DatetimeIndex) else df
    hr = L(S['hourly'].copy())
    hr.index.name = 'time_local'
    hr.round(4).to_csv(os.path.join(a.out, 'data', 'hourly.csv'))
    S['daily'].round(4).to_csv(os.path.join(a.out, 'data', 'daily.csv'))
    S['monthly'].round(4).to_csv(os.path.join(a.out, 'data', 'monthly.csv'), index=False)
    ev = S['storms'].copy()
    if len(ev):
        for c in ['start', 'end', 'peak_time']:
            ev[c] = ev[c].dt.strftime('%Y-%m-%d %H:%M')
    ev.round(3).to_csv(os.path.join(a.out, 'data', 'storms.csv'), index=False)
    downloads = [('📊 Spreadsheet (.xlsx)', xlsx), ('⬇️ Daily CSV', 'data/daily.csv'), ('⬇️ Hourly CSV', 'data/hourly.csv'),
                 ('⬇️ Storms CSV', 'data/storms.csv')]
    if SS.get('has'):
        sh = L(SS['hourly'].copy())
        sh.index.name = 'time_local'
        sh.round(4).to_csv(os.path.join(a.out, 'data', 'sensors_hourly.csv'))
        downloads.append(('⬇️ Sensors CSV', 'data/sensors_hourly.csv'))

    notice = ''
    try:
        import json
        with open(os.path.join(a.data, 'state.json')) as fh:
            errs = json.load(fh).get('last_errors') or []
        if errs:
            notice = ('⚠️ The last hourly download from Sofar had a problem, so some data may be missing: '
                      + ' · '.join(errs[:3]))
    except (OSError, ValueError):
        pass
    tides = fetch_tides()
    import plotly
    import plotly.offline
    ver = plotly.offline.get_plotlyjs_version()
    mdash.build_html(S, bd, os.path.join(a.out, 'index.html'),
                     plotly_js=plotly.offline.get_plotlyjs() if a.inline_plotly else None,
                     plotly_src=None if a.inline_plotly else f'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{ver}/plotly.min.js',
                     SS=SS, mode='web', downloads=downloads, notice=notice, tides=tides)
    export_latest(S, SS, os.path.join(a.out, 'data', 'latest.json'))
    open(os.path.join(a.out, '.nojekyll'), 'w').close()
    st = S['stats']
    print(f"✅ site built in {time.time() - t0:.0f}s — waves {st['first']:%b %d, %Y} → {st['last']:%b %d %Y %H:%M}, "
          f"{st['n_storms']} storms")


if __name__ == '__main__':
    main()
