"""
sensors.py — Smart Mooring (Bristlemouth) sensor data: subsurface water temperature,
dissolved oxygen, currents, and anything else hanging below the buoy.

Sofar delivers these as a long list of readings, each tagged with the sensor's position on the
mooring line, a data-type name, a unit type and units. We don't hard-code sensor models:
each series is recognised from those tags (keywords such as "oxygen", "temperature",
"current", "saturation", "°C", "mg/L", "m/s"). Anything unrecognised is still charted,
under its own name, so nothing is silently dropped.
"""
import glob
import math
import os
import re

import numpy as np
import pandas as pd

import buoy_core as bc

GROUPS = {  # key: (emoji, friendly name, sort order)
    'temp': ('🌡️', 'Water temperature', 1),
    'do_conc': ('🫧', 'Dissolved oxygen', 2),
    'do_sat': ('🫧', 'Dissolved oxygen saturation', 3),
    'cur_speed': ('🌀', 'Current speed', 4),
    'cur_dir': ('🧭', 'Current direction', 5),
    'cur_u': ('↔️', 'Current (east–west part)', 6),
    'cur_v': ('↕️', 'Current (north–south part)', 7),
    'pressure': ('📏', 'Water pressure / depth', 8),
    'sound': ('🔊', 'Underwater sound level', 8.5),
    'other': ('🔬', 'Other sensor', 9),
}
DO_HYPOXIC_MGL = 2.0     # widely used hypoxia threshold
DO_STRESS_MGL = 5.0      # below ~5 mg/L many fish & shellfish are stressed
UMOL_TO_MGL = 0.031998   # 1 µmol/L O2 = 0.032 mg/L


def classify(dtype, utype, units):
    t = f'{dtype} {utype}'.lower()
    u = str(units or '').lower().replace(' ', '')
    oxy = bool(re.search(r'oxygen|\bo2\b|_o2|o2_|dissolved_o|\bdo\b|_do_|optode|air_?sat', t))
    cur = bool(re.search(r'current|velocity|adcp|aquadopp|signature|flow', t))
    if re.search(r'°c|degc|celsius|°f', u) or re.search(r'temp', t):
        return 'temp'
    if (oxy and ('%' in u or 'sat' in t or 'percent' in t)) or ('saturation' in t and '%' in u and 'humid' not in t):
        return 'do_sat'
    if oxy and (re.search(r'mg/l|µmol|umol|ml/l|mmol|ppm', u) or 'conc' in t or u == ''):
        return 'do_conc'
    if cur or re.search(r'm/s|cm/s|mm/s', u):
        if re.search(r'east|_u\b|\bu_|\bvx?\b|_x\b', t):
            return 'cur_u'
        if re.search(r'north|_v\b|\bv_|\bvy\b|_y\b', t):
            return 'cur_v'
        if re.search(r'dir|heading|bearing', t) or re.search(r'deg|°', u):
            return 'cur_dir'
        return 'cur_speed'
    if re.search(r'sound|spl|hydrophone|db re', t + ' ' + u):
        return 'sound'
    if re.search(r'pressure|depth|dbar|kpa', t + ' ' + u):
        return 'pressure'
    return 'other'


def load(data_root):
    files = sorted(glob.glob(os.path.join(data_root, '**', 'sensors_*.csv'), recursive=True))
    if not files:
        return pd.DataFrame()
    d = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
    d['value'] = pd.to_numeric(d['value'], errors='coerce')
    d['t'] = pd.to_datetime(d['timestamp'], utc=True, errors='coerce', format='mixed')
    d = d.dropna(subset=['t', 'value'])
    for c in ['sensorPosition', 'data_type_name', 'unit_type', 'units']:
        d[c] = d[c].fillna('').astype(str)
    d = d.drop_duplicates(subset=['t', 'sensorPosition', 'data_type_name'])
    d['group'] = [classify(a, b, c) for a, b, c in zip(d['data_type_name'], d['unit_type'], d['units'])]
    return d


def _speed_to_ms(v, units):
    u = str(units).lower().replace(' ', '')
    return v / 100 if 'cm/s' in u else (v / 1000 if 'mm/s' in u else v)


def build(d):
    """Returns dict with hourly wide table, series catalogue, monthly table, insights."""
    if d is None or d.empty:
        return dict(has=False)
    series, cols = [], {}
    for (grp, pos, dtype, utype, units), g in d.groupby(['group', 'sensorPosition', 'data_type_name', 'unit_type', 'units']):
        v = g.set_index('t')['value'].sort_index()
        if grp in ('cur_speed', 'cur_u', 'cur_v'):
            v, units = _speed_to_ms(v, units), 'm/s'
        # light QC: drop wild outliers (> 6 robust SDs from the rolling median)
        med = v.rolling(25, center=True, min_periods=5).median()
        mad = (v - med).abs().rolling(25, center=True, min_periods=5).median() * 1.4826
        bad = (v - med).abs() > 6 * mad.clip(lower=1e-6) + (0.5 if grp == 'temp' else 0)
        v = v[~bad.fillna(False)]
        if grp == 'cur_dir':
            h = v.resample('1h').apply(lambda s: bc.circ_mean(s.values) if len(s) else np.nan)
        else:
            h = v.resample('1h').mean()
        emoji, name, order = GROUPS[grp]
        label = name if grp != 'other' else (dtype or utype or 'sensor').replace('_', ' ')
        sid = f'{grp}|{pos}|{dtype}'
        pos_txt = f'sensor #{pos}' if pos not in ('', 'nan') else 'sensor'
        series.append(dict(id=sid, group=grp, emoji=emoji, name=label, pos=pos, pos_txt=pos_txt, dtype=dtype,
                           units=units, order=order, n=int(v.count()), first=v.index.min(), last=v.index.max(),
                           latest=float(v.iloc[-1]) if len(v) else np.nan,
                           mean=bc.circ_mean(v.values) if grp == 'cur_dir' else float(v.mean()),
                           min=float(v.min()), max=float(v.max()), removed=int(bad.fillna(False).sum()),
                           col=f'{emoji} {label} ({units}) — {pos_txt}'))
        cols[sid] = h
    # currents from components → speed & direction
    for pos in {s['pos'] for s in series}:
        u = next((s for s in series if s['pos'] == pos and s['group'] == 'cur_u'), None)
        v_ = next((s for s in series if s['pos'] == pos and s['group'] == 'cur_v'), None)
        if u and v_ and not any(s['pos'] == pos and s['group'] == 'cur_speed' for s in series):
            U, V = cols[u['id']], cols[v_['id']]
            spd = np.hypot(U, V)
            dr = (np.degrees(np.arctan2(U, V)) % 360)
            for grp, h, units in [('cur_speed', spd, 'm/s'), ('cur_dir', dr, '°')]:
                emoji, name, order = GROUPS[grp]
                sid = f'{grp}|{pos}|derived'
                cols[sid] = h
                hv = h.dropna()
                series.append(dict(id=sid, group=grp, emoji=emoji, name=name, pos=pos, pos_txt=f'sensor #{pos}',
                                   dtype='derived from components', units=units, order=order, n=int(hv.count()),
                                   first=hv.index.min(), last=hv.index.max(),
                                   latest=float(hv.iloc[-1]) if len(hv) else np.nan,
                                   mean=bc.circ_mean(hv.values) if grp == 'cur_dir' else float(hv.mean()),
                                   min=float(hv.min()), max=float(hv.max()), removed=0,
                                   col=f'{emoji} {name} ({units}) — sensor #{pos}'))
    series.sort(key=lambda s: (s['order'], s['pos'], s['dtype']))
    wide = pd.DataFrame({s['col']: cols[s['id']] for s in series}).sort_index()
    # monthly
    loc = wide.copy()
    loc.index = bc.local(loc.index).tz_localize(None)
    rows = []
    for s in series:
        col = loc[s['col']].dropna()
        for per, g in col.groupby(col.index.to_period('M')):
            if s['group'] == 'cur_dir':
                rows.append(dict(month=per.strftime('%b %Y'), series=s['col'], mean=bc.circ_mean(g.values),
                                 min=np.nan, max=np.nan, hours=int(g.count())))
            else:
                rows.append(dict(month=per.strftime('%b %Y'), series=s['col'], mean=g.mean(), min=g.min(),
                                 max=g.max(), hours=int(g.count())))
    monthly = pd.DataFrame(rows)
    return dict(has=True, series=series, hourly=wide, monthly=monthly, raw_n=len(d),
                first=d['t'].min(), last=d['t'].max())


def _find(SS, grp):
    return [s for s in SS['series'] if s['group'] == grp]


def insights(SS, S=None):
    """Plain-English findings about the subsurface data."""
    if not SS.get('has'):
        return []
    out, H = [], SS['hourly']
    for s in _find(SS, 'temp')[:2]:
        col = H[s['col']].dropna()
        if col.empty:
            continue
        loc = col.copy()
        loc.index = bc.local(loc.index)
        out.append(('🌡️', f"Water temperature ({s['pos_txt']}): {s['min']:.1f}–{s['max']:.1f} °C",
                    f"Coldest {loc.idxmin():%b %d, %Y}, warmest {loc.idxmax():%b %d, %Y}. "
                    f"Latest reading {s['latest']:.1f} °C ({s['latest'] * 9 / 5 + 32:.0f} °F)."))
    for s in _find(SS, 'do_conc')[:2]:
        col = H[s['col']].dropna()
        mgl = col * UMOL_TO_MGL if re.search(r'mol', s['units'].lower()) else col
        if 'mg' in s['units'].lower() or 'mol' in s['units'].lower():
            low = (mgl < DO_STRESS_MGL).mean() * 100
            hyp = (mgl < DO_HYPOXIC_MGL).mean() * 100
            out.append(('🫧', f"Dissolved oxygen ({s['pos_txt']}): {'healthy' if low < 1 else 'dips below 5 mg/L'}",
                        f"Average {mgl.mean():.1f} mg/L (range {mgl.min():.1f}–{mgl.max():.1f}). Below the 5 mg/L stress line "
                        f"{low:.1f}% of hours; hypoxic (< 2 mg/L) {hyp:.1f}% of hours. Colder water holds more oxygen, so "
                        f"expect a summer low."))
        else:
            out.append(('🫧', f"Dissolved oxygen ({s['pos_txt']})",
                        f"Average {s['mean']:.2f} {s['units']} (range {s['min']:.2f}–{s['max']:.2f})."))
    for s in _find(SS, 'do_sat')[:1]:
        out.append(('🫧', f"Oxygen saturation averages {s['mean']:.0f}%",
                    f"Range {s['min']:.0f}–{s['max']:.0f}%. Over 100% usually means algae are photosynthesising; "
                    f"well under 100% means oxygen is being used up faster than it's replaced."))
    for s in _find(SS, 'cur_speed')[:1]:
        col = H[s['col']].dropna()
        txt = (f"Average {s['mean'] * 100:.0f} cm/s, fastest {s['max'] * 100:.0f} cm/s "
               f"({s['max'] * 1.944:.1f} knots).")
        if S is not None and len(S['storms']):
            ev = S['storms']
            mask = pd.Series(False, index=col.index)
            for r in ev.itertuples():
                mask |= (col.index >= r.start.tz_convert('UTC')) & (col.index <= r.end.tz_convert('UTC'))
            if mask.any() and (~mask).any():
                ratio = col[mask].mean() / max(col[~mask].mean(), 1e-9)
                txt += (f" During storm events currents ran {ratio:.1f}× their usual speed — "
                        "that's when sediment gets moved.")
        dirs = _find(SS, 'cur_dir')
        if dirs:
            d = dirs[0]
            txt += f" Typical direction {bc.compass(d['mean'])} ({d['mean']:.0f}°, as reported by the sensor)."
        out.append(('🌀', f"Currents ({s['pos_txt']})", txt))
    return out
