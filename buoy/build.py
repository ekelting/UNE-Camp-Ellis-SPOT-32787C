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
    SS = sn.build(sn.load(a.data))
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
    import plotly
    import plotly.offline
    ver = plotly.offline.get_plotlyjs_version()
    mdash.build_html(S, bd, os.path.join(a.out, 'index.html'),
                     plotly_js=plotly.offline.get_plotlyjs() if a.inline_plotly else None,
                     plotly_src=None if a.inline_plotly else f'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{ver}/plotly.min.js',
                     SS=SS, mode='web', downloads=downloads, notice=notice)
    open(os.path.join(a.out, '.nojekyll'), 'w').close()
    st = S['stats']
    print(f"✅ site built in {time.time() - t0:.0f}s — waves {st['first']:%b %d, %Y} → {st['last']:%b %d %Y %H:%M}, "
          f"{st['n_storms']} storms")


if __name__ == '__main__':
    main()
