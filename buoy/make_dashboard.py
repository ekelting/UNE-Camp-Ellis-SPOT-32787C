"""
make_dashboard.py - builds Camp_Ellis_Buoy_Dashboard.html, a single self-contained
page (works offline, just double-click it). Called by update_buoy_report.py.
"""
import datetime as dt
import html
import json

import numpy as np
import pandas as pd

import buoy_core as bc

# palette (validated reference palette from the dataviz method)
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = (
    '#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948')
SEQ = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

VIBES = [(0.3, '😴', 'Glassy', 'Flat, lake-like water.'),
         (0.6, '🙂', 'Gentle', 'Small, easy waves.'),
         (1.0, '🌊', 'Lively', 'Noticeable waves rolling in.'),
         (1.5, '💪', 'Rough', 'Big for this bay — whitecaps likely.'),
         (2.5, '⚠️', 'Stormy', 'Storm waves — erosion weather.'),
         (99, '🌀', 'Huge storm', 'Among the biggest seas this buoy sees.')]


def vibe(hs):
    for lim, e, name, desc in VIBES:
        if hs < lim:
            return e, name, desc
    return VIBES[-1][1:]


def _clean(v):
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _ts(idx):
    return [t.strftime('%Y-%m-%d %H:%M') for t in idx]


def _fig(traces, layout):
    return {'data': traces, 'layout': layout}


def build_figs(S, bd):
    st, mo, ev, hr, dy = S['stats'], S['monthly'], S['storms'], S['hourly'], S['daily']
    mo = mo[~mo['partial']].reset_index(drop=True)
    figs = {}
    H = hr.copy()
    H.index = bc.local(H.index).tz_localize(None)

    # 1) timeline --------------------------------------------------------------
    dmax = H['hs'].resample('1D').max()
    shapes = []
    for r in ev.itertuples() if len(ev) else []:
        shapes.append(dict(type='rect', xref='x', yref='paper', x0=r.start.strftime('%Y-%m-%d %H:%M'),
                           x1=(r.end + pd.Timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'), y0=0, y1=1,
                           fillcolor=ORANGE, opacity=0.2, line_width=0, layer='below'))
    figs['timeline'] = _fig([
        dict(type='scatter', mode='lines', name='Hourly wave height', x=_ts(H.index), y=_clean(H['hs'].tolist()),
             line=dict(color=BLUE, width=1.2), meta='len',
             hovertemplate='%{x|%b %d %Y, %H:%M}<br><b>%{y:.1f} UNIT</b><extra></extra>'),
        dict(type='scatter', mode='markers', name='Daily peak', x=[d.strftime('%Y-%m-%d 12:00') for d in dmax.index],
             y=_clean(dmax.tolist()), marker=dict(color=ORANGE, size=5), meta='len', visible='legendonly',
             hovertemplate='%{x|%b %d %Y}: peak %{y:.1f} UNIT<extra></extra>'),
    ], dict(shapes=shapes, yaxis=dict(title='Wave height (UNIT)', rangemode='tozero'),
            xaxis=dict(rangeslider=dict(visible=True, thickness=0.07), type='date',
                       rangeselector=dict(buttons=[dict(count=7, label='1 wk', step='day', stepmode='backward'),
                                                   dict(count=1, label='1 mo', step='month', stepmode='backward'),
                                                   dict(count=3, label='3 mo', step='month', stepmode='backward'),
                                                   dict(step='all', label='All')])),
            legend=dict(orientation='h', y=1.12, x=1, xanchor='right'), height=430))

    # 2) calendar heatmap -------------------------------------------------------
    d = dy.loc[dy['n_waves_obs'] >= 8, 'hs_mean'].copy()
    d.index = pd.to_datetime(d.index)
    months = pd.period_range(d.index.min(), d.index.max(), freq='M')
    z, txt = [], []
    for p in months:
        row, trow = [], []
        for day in range(1, 32):
            try:
                t = pd.Timestamp(year=p.year, month=p.month, day=day)
            except ValueError:
                row.append(None)
                trow.append('')
                continue
            v = d.get(t, np.nan)
            row.append(None if pd.isna(v) else round(float(v), 3))
            trow.append(t.strftime('%a %b %d, %Y'))
        z.append(row)
        txt.append(trow)
    figs['calendar'] = _fig([dict(
        type='heatmap', z=z, x=list(range(1, 32)), y=[p.strftime('%b %Y') for p in months], text=txt,
        colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)], xgap=2, ygap=2, meta='len',
        zmin=0, zmax=float(np.nanpercentile(d.values, 98)),
        colorbar=dict(title=dict(text='UNIT', side='right'), thickness=10, len=0.9),
        hovertemplate='%{text}<br>average wave height <b>%{z:.1f} UNIT</b><extra></extra>')],
        dict(margin=dict(l=80, r=20, t=20, b=50), yaxis=dict(autorange='reversed', fixedrange=True), xaxis=dict(title='Day of month', dtick=5, fixedrange=True),
             height=max(260, 34 * len(months) + 90)))

    # 3) monthly ---------------------------------------------------------------
    figs['monthly'] = _fig([
        dict(type='bar', name='Average', x=mo['label'].tolist(), y=_clean(mo['hs_mean'].tolist()), marker=dict(color=BLUE),
             meta='len', hovertemplate='%{x}: average %{y:.1f} UNIT<extra></extra>'),
        dict(type='bar', name='Rough days (top 10%)', x=mo['label'].tolist(), y=_clean(mo['hs_p90'].tolist()),
             marker=dict(color=AQUA), meta='len', hovertemplate='%{x}: 90th percentile %{y:.1f} UNIT<extra></extra>'),
        dict(type='bar', name='Biggest', x=mo['label'].tolist(), y=_clean(mo['hs_max'].tolist()), marker=dict(color=ORANGE),
             meta='len', hovertemplate='%{x}: max %{y:.1f} UNIT<extra></extra>'),
    ], dict(barmode='group', bargap=0.25, bargroupgap=0.08, yaxis=dict(title='Wave height (UNIT)'),
            legend=dict(orientation='h', y=1.14, x=1, xanchor='right'), height=380))
    figs['energy'] = _fig([dict(
        type='bar', x=mo['label'].tolist(), y=_clean((mo['energy_mj_m'] / 3.6).tolist()), marker=dict(color=BLUE),
        text=[f"{v / 3.6:,.0f}" for v in mo['energy_mj_m']], textposition='outside', cliponaxis=False,
        customdata=_clean(mo['storms'].tolist()),
        hovertemplate='%{x}<br><b>%{y:,.0f} kWh</b> per metre of coastline<br>%{customdata} storm event(s)<extra></extra>')],
        dict(yaxis=dict(title='kWh per metre of wave crest'), height=340, showlegend=False))

    # 4) wave rose --------------------------------------------------------------
    ro = S['rose']
    rose_tr = []
    import re
    def _ftlabel(c):
        nums = [float(x) for x in re.findall(r'[\d.]+', c)]
        f = [f"{bc.ft(x):.0f}" if bc.ft(x) >= 3 else f"{bc.ft(x):.1f}".rstrip('0').rstrip('.') for x in nums]
        return (f"{f[0]}–{f[1]} ft" if len(f) == 2 else f"≥{f[0]} ft") + f"  ({c})"
    for i, col in enumerate(ro.columns):
        rose_tr.append(dict(type='barpolar', name=_ftlabel(col), r=_clean(ro[col].tolist()), theta=list(ro.index),
                            marker=dict(color=SEQ[min(i + 1, len(SEQ) - 1)], line=dict(width=1, color='rgba(0,0,0,0)')),
                            hovertemplate='from %{theta}: %{r:.1f}% of the time<extra>' + col + '</extra>'))
    figs['rose'] = _fig(rose_tr, dict(
        polar=dict(domain=dict(x=[0, 0.62]), angularaxis=dict(direction='clockwise', rotation=90), radialaxis=dict(ticksuffix='%', angle=90)),
        legend=dict(title=dict(text='Wave height'), orientation='v', x=0.72, y=0.5, yanchor='middle'), height=420))

    # 5) pressure + waves (stacked panels, shared time axis — no dual axis) -----
    P = bd.met['pres'].resample('1h').mean()
    P.index = bc.local(P.index).tz_localize(None)
    figs['pressure'] = _fig([
        dict(type='scattergl', mode='lines', name='Pressure', x=_ts(P.index), y=_clean(P.tolist()),
             line=dict(color=VIOLET, width=1.4), xaxis='x', yaxis='y2',
             hovertemplate='%{x|%b %d %H:%M}<br>%{y:.0f} hPa<extra></extra>'),
        dict(type='scattergl', mode='lines', name='Wave height', x=_ts(H.index), y=_clean(H['hs'].tolist()),
             line=dict(color=BLUE, width=1.1), xaxis='x', yaxis='y', meta='len',
             hovertemplate='%{x|%b %d %H:%M}<br>%{y:.1f} UNIT<extra></extra>'),
    ], dict(yaxis2=dict(title='Pressure (hPa)', domain=[0.55, 1], anchor='x'), yaxis=dict(title='Waves (UNIT)', domain=[0, 0.45]),
            showlegend=False, height=460))

    # 6) spectrum by season -----------------------------------------------------
    sp = bd.spec
    if len(sp):
        sp = sp.copy()
        sp.index = pd.DatetimeIndex(sp.index)
        f = np.array(sp.columns, float)
        keep = (f >= 0.04) & (f <= 0.5)
        seasons = {'❄️ Winter (Dec–Feb)': [12, 1, 2], '🌱 Spring (Mar–May)': [3, 4, 5],
                   '☀️ Summer (Jun–Aug)': [6, 7, 8], '🍂 Fall (Sep–Nov)': [9, 10, 11]}
        cols = [BLUE, AQUA, ORANGE, YELLOW]
        tr = []
        for (name, ms), c in zip(seasons.items(), cols):
            sub = sp[sp.index.month.isin(ms)]
            if len(sub) < 96:
                continue
            mean = sub.mean().values[keep]
            tr.append(dict(type='scatter', mode='lines', name=name, x=_clean((1 / f[keep]).tolist()),
                           y=_clean(mean.tolist()), line=dict(color=c, width=2.2, shape='spline'),
                           hovertemplate='%{x:.1f}-second waves: %{y:.3f} m²/Hz<extra>' + name + '</extra>'))
        figs['spectrum'] = _fig(tr, dict(
            xaxis=dict(title='Wave period — seconds between crests (log scale)', type='log',
                       tickvals=[2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25]),
            yaxis=dict(title='Energy density (m²/Hz)'), legend=dict(orientation='h', y=1.14, x=1, xanchor='right'),
            height=380))

    # 7) buoy health ------------------------------------------------------------
    M = bd.met[['humid', 'batt']].resample('6h').mean()
    M.index = bc.local(M.index).tz_localize(None)
    figs['health'] = _fig([
        dict(type='scatter', mode='lines', name='Hull humidity', x=_ts(M.index), y=_clean(M['humid'].tolist()),
             line=dict(color=AQUA, width=2), yaxis='y2', hovertemplate='%{x|%b %d}: %{y:.0f}% humidity<extra></extra>'),
        dict(type='scatter', mode='lines', name='Battery', x=_ts(M.index), y=_clean(M['batt'].tolist()),
             line=dict(color=GREEN, width=2), yaxis='y', hovertemplate='%{x|%b %d}: %{y:.2f} V<extra></extra>'),
    ], dict(yaxis2=dict(title='Humidity (%)', domain=[0.55, 1], rangemode='tozero', anchor='x'),
            yaxis=dict(title='Battery (V)', domain=[0, 0.45]), showlegend=False, height=380))
    return figs


CAT = [BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED]


def build_sensor_figs(SS):
    """Figures + HTML for the Smart Mooring section. Returns (figs, html_block, tiles)."""
    import sensors as sn
    esc = html.escape
    if not SS or not SS.get('has'):
        block = ('<div class="card"><p style="margin:0">🔌 No Smart Mooring sensor data has arrived yet. Once Sofar reports '
                 'subsurface temperature, dissolved oxygen or current readings for this buoy, charts appear here automatically.</p></div>')
        return {}, block, []
    figs, parts, tiles = {}, [], []
    H = SS['hourly'].copy()
    H.index = bc.local(H.index).tz_localize(None)
    x = _ts(H.index)
    groups = []
    for s in SS['series']:
        if s['group'] not in groups:
            groups.append(s['group'])
    for g in groups:
        ser = [s for s in SS['series'] if s['group'] == g]
        if g == 'cur_dir':
            continue
        emoji, name, _ = sn.GROUPS[g]
        if g == 'other':
            name = ', '.join(sorted({s['name'] for s in ser}))[:80]
        units = ser[0]['units']
        tr = []
        unit_lbl = esc('cm/s' if g == 'cur_speed' else units)
        for i, s in enumerate(ser):
            yy = H[s['col']] * (100 if g == 'cur_speed' else 1)
            if g == 'cur_dir':
                continue
            dly = yy.resample('1D').mean()
            nm = s['pos_txt'] + ('' if g != 'other' else f" · {s['name']}")
            tr.append(dict(type='scatter', mode='lines', name=nm + ' (daily avg)', legendgroup=nm,
                           x=[d.strftime('%Y-%m-%d 12:00') for d in dly.index], y=_clean(dly.tolist()),
                           line=dict(color=CAT[i % len(CAT)], width=2.2), connectgaps=False,
                           hovertemplate='%{x|%b %d %Y}<br>daily average <b>%{y:.2f} ' + unit_lbl + '</b><extra>'
                           + esc(s['pos_txt']) + '</extra>'))
            tr.append(dict(type='scatter', mode='lines', name=nm + ' (hourly)', legendgroup=nm, visible='legendonly',
                           x=x, y=_clean(yy.tolist()), line=dict(color=CAT[i % len(CAT)], width=1), opacity=0.6,
                           connectgaps=False,
                           hovertemplate='%{x|%b %d %Y, %H:%M}<br><b>%{y:.2f} ' + unit_lbl + '</b><extra>'
                           + esc(s['pos_txt']) + '</extra>'))
        shapes = []
        ul = units.lower()
        if g == 'do_conc' and ('mg' in ul or 'mol' in ul):
            k = 1 / sn.UMOL_TO_MGL if 'mol' in ul else 1
            for lvl, lab, col in [(sn.DO_STRESS_MGL * k, 'stress (5 mg/L)', YELLOW), (sn.DO_HYPOXIC_MGL * k, 'hypoxic (2 mg/L)', RED)]:
                shapes.append(dict(type='line', xref='paper', x0=0, x1=1, y0=lvl, y1=lvl,
                                   line=dict(color=col, width=1.5, dash='dash')))
                tr.append(dict(type='scatter', mode='lines', x=[None], y=[None], name=lab,
                               line=dict(color=col, width=1.5, dash='dash'), hoverinfo='skip'))
        fid = f'sens_{g}'
        figs[fid] = _fig(tr, dict(shapes=shapes, yaxis=dict(title=f"{name} ({'cm/s' if g == 'cur_speed' else units})"),
                                  xaxis=dict(type='date'), margin=dict(l=60, r=20, t=80, b=40),
                                  legend=dict(orientation='h', y=1.02, yanchor='bottom', x=1, xanchor='right'),
                                  height=380, showlegend=True))
        parts.append(f'<div><h3>{emoji} {esc(name)}</h3><div class="card"><div id="{fid}" class="chart"></div>'
                     '<p class="t-sub" style="margin:6px 0 0">Daily averages — click “hourly” in the legend for full detail.</p></div></div>')
        s0 = ser[0]
        if g == 'temp':
            tiles.append(('🌡️', f'Water temp ({s0["pos_txt"]})', f'{s0["latest"]:.1f} °C', f'{s0["latest"] * 9 / 5 + 32:.0f} °F'))
        elif g == 'do_conc':
            mg = f'≈ {s0["latest"] * sn.UMOL_TO_MGL:.1f} mg/L · ' if 'mol' in units.lower() else ''
            tiles.append(('🫧', 'Dissolved oxygen', f'{s0["latest"]:.0f} {units}' if mg else f'{s0["latest"]:.1f} {units}',
                          mg + s0['pos_txt']))
        elif g == 'do_sat':
            tiles.append(('🫧', 'Oxygen saturation', f'{s0["latest"]:.0f}%', s0['pos_txt']))
        elif g == 'cur_speed':
            tiles.append(('🌀', 'Current speed', f'{s0["latest"] * 100:.0f} cm/s', f'{s0["latest"] * 1.944:.1f} knots'))
    # current rose
    spd = [s for s in SS['series'] if s['group'] == 'cur_speed']
    drs = [s for s in SS['series'] if s['group'] == 'cur_dir']
    if spd and drs:
        sp, dr = H[spd[0]['col']] * 100, H[drs[0]['col']]
        ok = sp.notna() & dr.notna()
        if ok.sum() > 10:
            sector = ((dr[ok] % 360 + 11.25) // 22.5 % 16).astype(int).map(lambda i: bc.COMPASS16[i])
            bins = [0, 5, 10, 20, 40, 1e9]
            labs = ['0–5 cm/s', '5–10 cm/s', '10–20 cm/s', '20–40 cm/s', '≥40 cm/s']
            cut = pd.cut(sp[ok], bins=bins, labels=labs, right=False)
            tab = (pd.crosstab(sector, cut, normalize=True) * 100).reindex(bc.COMPASS16).fillna(0)
            tr = [dict(type='barpolar', name=c, r=_clean(tab[c].tolist()) if c in tab else [0] * 16, theta=bc.COMPASS16,
                       marker=dict(color=SEQ[min(i + 2, len(SEQ) - 1)]),
                       hovertemplate='%{theta}: %{r:.1f}% of hours<extra>' + c + '</extra>') for i, c in enumerate(labs)]
            figs['sens_rose'] = _fig(tr, dict(
                polar=dict(domain=dict(x=[0, 0.62]), angularaxis=dict(direction='clockwise', rotation=90),
                           radialaxis=dict(ticksuffix='%', angle=90)),
                legend=dict(title=dict(text='Current speed'), x=0.72, y=0.5, yanchor='middle'), height=380))
            parts.append('<div><h3>🧭 Which way the water flows</h3><div class="card"><div id="sens_rose" class="chart"></div>'
                         '<p class="t-sub" style="margin:6px 0 0">Direction as reported by the current sensor '
                         '(most current meters report the direction the water flows <i>toward</i>).</p></div></div>')
    rng = f"{bc.local(pd.DatetimeIndex([SS['first']]))[0]:%b %d, %Y} → {bc.local(pd.DatetimeIndex([SS['last']]))[0]:%b %d, %Y}"
    table = ''.join(f"<tr><td>{s['emoji']} {esc(s['name'])}</td><td>{esc(s['pos_txt'])}</td><td>{esc(s['units'])}</td>"
                    f"<td>{s['latest']:.2f}</td><td>{s['mean']:.2f}</td><td>{s['min']:.2f}</td><td>{s['max']:.2f}</td>"
                    f"<td>{s['n']:,}</td><td>{s['removed']:,}</td><td><small>{esc(s['dtype'])}</small></td></tr>" for s in SS['series'])
    block = (f'<p class="lede">{SS["raw_n"]:,} sensor readings, {rng}. Hourly averages shown.</p>'
             f'<div class="grid two">{"".join(parts)}</div>'
             '<details><summary>📋 Every sensor channel</summary><div class="tablewrap"><table><thead><tr><th>Measures</th>'
             '<th>Position</th><th>Units</th><th>Latest</th><th>Average</th><th>Min</th><th>Max</th><th>Readings</th><th>Outliers removed</th>'
             f'<th>Sofar name</th></tr></thead><tbody>{table}</tbody></table></div></details>')
    return figs, block, tiles


def build_html(S, bd, path, plotly_js=None, SS=None, mode='local', plotly_src=None, downloads=None):
    st, ev, lt = S['stats'], S['storms'], S['stats']['latest']
    figs = build_figs(S, bd)
    sfigs, sensor_block, stiles = build_sensor_figs(SS)
    figs.update(sfigs)
    e, vname, vdesc = vibe(lt['hs'])
    now = pd.Timestamp.now(tz=bc.LOCAL_TZ)
    age_days = (now - lt['time']) / pd.Timedelta(days=1)
    stale = age_days > 1.5
    ptrend = lt['pres_3h_change']
    parrow = '⬇️ falling' if ptrend < -1 else ('⬆️ rising' if ptrend > 1 else '➡️ steady')
    esc = html.escape

    def tile(emoji, label, value, sub='', unit_attr=None):
        v = f'<span class="len" data-m="{unit_attr}">{value}</span>' if unit_attr is not None else value
        return (f'<div class="tile"><div class="t-emoji">{emoji}</div><div class="t-label">{esc(label)}</div>'
                f'<div class="t-value">{v}</div><div class="t-sub">{sub}</div></div>')

    now_tiles = ''.join([
        tile(e, 'Wave height', f'{bc.ft(lt["hs"]):.1f} ft', f'{vname} — {esc(vdesc)}', lt['hs']),
        tile('⏱️', 'Wave rhythm', f'{lt["tp"]:.0f} s' if np.isfinite(lt['tp']) else '—',
             'between the biggest crests' + (' — long swell 🏄' if lt['tp'] >= 10 else '')),
        tile(f'<span class="arrow" style="transform:rotate({(lt["dm"] + 180) % 360:.0f}deg)">⬆</span>',
             'Waves coming from', f'{lt["dir"]}', f'{lt["dm"]:.0f}° — heading toward {bc.compass((lt["dm"] + 180) % 360)}')
        if np.isfinite(lt['dm']) else tile('🧭', 'Waves coming from', '—'),
        tile('🌬️', 'Air pressure', f'{lt["pres"]:.0f} hPa', f'{parrow} over 3 h') if np.isfinite(lt['pres']) else tile('🌬️', 'Air pressure', '—'),
    ] + [tile(*t) for t in stiles] + [
        tile('🔋', 'Buoy battery', f'{lt["batt"]:.2f} V', 'full' if lt['batt'] >= 3.8 else ('mid-level' if lt['batt'] >= 3.6 else 'low ⚠️'))
        if np.isfinite(lt['batt']) else tile('🔋', 'Buoy battery', '—'),
    ])
    season_tiles = ''.join([
        tile('📆', 'Days on watch', f'{st["days"]:.0f}', f'since {st["first"]:%b %d, %Y}'),
        tile('📡', 'Wave readings', f'{st["n_wave_obs"]:,}', f'{st["coverage_pct"]:.0f}% of hours covered'),
        tile('🏆', 'Biggest waves', f'{bc.ft(st["hs_max"]):.1f} ft', f'{st["hs_max_time"]:%b %d, %Y}', st['hs_max']),
        tile('🌀', 'Storm events', f'{st["n_storms"]}', f'{st["storm_hours"]:.0f} stormy hours'),
        tile('😴', 'Calm time', f'{st["pct_calm"]:.0f}%', 'waves under 1 ft'),
        tile('🏄', 'Waves ridden', f'{st["n_waves"] / 1e6:.1f} M', 'estimated, by the buoy itself'),
    ])
    cards = ''.join(f'<div class="card insight"><div class="i-emoji">{em}</div><div><h4>{esc(h)}</h4><p>{esc(t)}</p></div></div>'
                    for em, h, t in bc.insights(S) + (__import__('sensors').insights(SS, S) if SS else []))
    if len(ev):
        top = ev.sort_values('peak_hs', ascending=False).reset_index(drop=True)
        medals = ['🥇', '🥈', '🥉']
        srows = ''.join(
            f'<tr><td>{medals[i] if i < 3 else i + 1}</td><td>{r.start:%b %d, %Y}</td><td>{r.hours:.0f} h</td>'
            f'<td><b class="len" data-m="{r.peak_hs}">{bc.ft(r.peak_hs):.1f} ft</b></td>'
            f'<td>{r.tp_at_peak:.0f} s</td><td>{r.dir}</td><td>{r.min_pres:.0f}</td><td>{r.energy_mj_m / 3.6:,.0f}</td></tr>'
            for i, r in enumerate(top.itertuples()))
    else:
        srows = '<tr><td colspan="8">No storm events yet 🎉</td></tr>'
    qc = ''.join(f'<li>{esc(n)}</li>' for n in bc.qc_notes(S))
    if mode == 'web':
        stale_banner = (f'<div class="banner">📡 The buoy hasn\'t reported new waves since <b>{lt["time"]:%b %d, %Y %I:%M %p}</b> '
                        f'({age_days:.1f} days). It may be offline, or Sofar may be delayed — this page will catch up automatically.</div>'
                        ) if stale else ''
        howto = ('<li>This page rebuilds itself <b>every hour</b> on GitHub: it asks Sofar for anything new (waves, spectra, pressure, '
                 'buoy health and Smart Mooring sensors), saves it to the repository, and republishes.</li>'
                 f'<li>Nothing to do on your end. To force an update now: <a href="{bc.REPO_URL}/actions">GitHub → Actions</a> → '
                 '<b>Update buoy dashboard</b> → <b>Run workflow</b>.</li><li>Google Sheets that always stay current: <code>=IMPORTDATA("' + bc.SITE_URL + 'data/daily.csv")</code> '
                 '(see the downloads at the top).</li>')
    else:
        stale_banner = (f'<div class="banner">📦 These numbers run through <b>{lt["time"]:%b %d, %Y}</b> ({age_days:.0f} days ago). '
                        'Run <b>Set Up Auto Update</b> once to keep it current automatically, or drop in the newest monthly CSV and '
                        'double-click <b>Update Buoy Report</b>.</div>') if stale else ''
        howto = ('<li>Download the newest month from the Spotter dashboard and drop the CSV into the buoy folder.</li>'
                 '<li>Double-click <b>Update Buoy Report.bat</b> in the buoy folder.</li>'
                 '<li>Hands-free: <b>Set Up Auto Update.bat</b> once with your Sofar API token.</li>')
    dl = ''
    if downloads:
        dl = '<div class="chips" style="margin-top:12px">' + ''.join(
            f'<a class="chip dl" href="{esc(href)}" download>{esc(lbl)}</a>' for lbl, href in downloads) + '</div>'
    if plotly_src:
        plotly_tag = f'<script src="{plotly_src}"></script>'
    else:
        plotly_tag = f'<script>{plotly_js}</script>'

    fig_json = json.dumps(figs, default=str, ensure_ascii=False)
    page = TEMPLATE
    for k, v in {
        '%%PLOTLY_TAG%%': plotly_tag, '%%FIGS%%': fig_json, '%%SENSORS%%': sensor_block, '%%HOWTO%%': howto,
        '%%DOWNLOADS%%': dl,
        '%%FOOTSRC%%': (f'from <a href="{bc.REPO_URL}">{bc.GITHUB_USER}/{bc.GITHUB_REPO}</a> on GitHub' if mode == 'web'
                        else 'by <code>buoy_tools/update_buoy_report.py</code>'), '%%NOW_TILES%%': now_tiles, '%%SEASON_TILES%%': season_tiles,
        '%%CARDS%%': cards, '%%STORM_ROWS%%': srows, '%%QC%%': qc, '%%STALE%%': stale_banner,
        '%%SPOTTER%%': esc(st['spotter']), '%%SITE%%': esc(st['site']),
        '%%LATEST%%': f'{lt["time"]:%A, %b %d %Y at %I:%M %p}',
        '%%GENERATED%%': f'{pd.Timestamp.now(tz=bc.LOCAL_TZ):%b %d, %Y %I:%M %p %Z}',
        '%%STORM_DEF%%': f'waves ≥ {bc.ft(bc.STORM_HS_M):.1f} ft ({bc.STORM_HS_M:g} m) for {bc.STORM_MIN_HOURS}+ hours',
        '%%VIBES%%': ''.join(f'<span class="chip">{v[1]} {v[2]} <small class="len" data-m="{v[0]}" data-lt="1">'
                             f'&lt;{bc.ft(v[0]):.0f} ft</small></span>' for v in VIBES[:-1])
                     + f'<span class="chip">{VIBES[-1][1]} {VIBES[-1][2]}</span>',
    }.items():
        page = page.replace(k, v)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(page)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Camp Ellis Buoy</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌊</text></svg>">
<style>
:root{--bg:#f5f8fa;--card:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e3e8ec;--accent:#1c5cab;
--hero1:#0d366b;--hero2:#1d7a8c;--chip:#eef4fb;--chip2:#b7d3f6;--warn:#fff4e0;--warnline:#eda100;--grid:#e8ecef}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111213;--card:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;
--muted:#8f8e86;--line:#2c2d2e;--accent:#86b6ef;--hero1:#0d2a4d;--hero2:#145a66;--chip:#23282e;--chip2:#1c5cab;--warn:#2d2616;--warnline:#c98500;--grid:#2a2b2c}}
:root[data-theme="dark"]{--bg:#111213;--card:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--muted:#8f8e86;--line:#2c2d2e;--accent:#86b6ef;
--hero1:#0d2a4d;--hero2:#145a66;--chip:#23282e;--chip2:#1c5cab;--warn:#2d2616;--warnline:#c98500;--grid:#2a2b2c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px 48px}
header.hero{background:linear-gradient(135deg,var(--hero1),var(--hero2));color:#fff;padding:36px 16px 70px;position:relative;overflow:hidden}
header.hero .wrap{padding-bottom:0}
header h1{margin:0;font-size:clamp(26px,4vw,40px);letter-spacing:-.5px}
header p{margin:6px 0 0;opacity:.88}
.waves{position:absolute;left:0;right:0;bottom:-2px;height:60px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.btn{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.35);color:#fff;border-radius:999px;padding:6px 14px;cursor:pointer;font:inherit;font-size:14px}
.btn[aria-pressed="true"]{background:#fff;color:#0d366b;font-weight:600}
section{margin-top:28px}
h2{font-size:22px;margin:0 0 4px}
h3{font-size:17px;margin:14px 0 8px}
.lede{color:var(--ink2);margin:0 0 14px}
.grid{display:grid;gap:12px}
.tiles{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.now{margin-top:-52px;position:relative}
.t-emoji{font-size:26px;line-height:1.1}
.arrow{display:inline-block;font-size:24px;color:var(--accent)}
.t-label{color:var(--ink2);font-size:13px;margin-top:6px}
.t-value{font-size:28px;font-weight:700;letter-spacing:-.5px}
.t-sub{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
a.chip.dl{background:rgba(255,255,255,.14);color:#fff;text-decoration:none;border:1px solid rgba(255,255,255,.35)}
.chip{background:var(--chip);border-radius:999px;padding:3px 10px;font-size:13px;color:var(--ink2)}
.insights{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.insight{display:flex;gap:12px}
.i-emoji{font-size:28px}
.insight h4{margin:0 0 4px;font-size:15px}
.insight p{margin:0;color:var(--ink2);font-size:14px}
.chart{width:100%;min-height:260px}
.two{grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr))}
.banner{background:var(--warn);border-left:4px solid var(--warnline);border-radius:10px;padding:10px 14px;margin-top:16px;color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--ink2);font-weight:600;font-size:13px}
.tablewrap{overflow-x:auto}
details{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin-top:12px}
summary{cursor:pointer;font-weight:600}
details li{color:var(--ink2);margin:6px 0;font-size:14px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;font-size:14px}
dt{font-weight:600}dd{margin:0;color:var(--ink2)}
footer{color:var(--muted);font-size:13px;margin-top:36px}
a{color:var(--accent)}
code{background:var(--chip);padding:1px 5px;border-radius:5px}
@media (max-width:560px){dl{grid-template-columns:1fr}.t-value{font-size:24px}}
</style></head>
<body>
<header class="hero"><div class="wrap">
  <h1>🌊 The Camp Ellis Wave Buoy</h1>
  <p>What the ocean has been up to off %%SITE%% · Sofar Spotter <b>%%SPOTTER%%</b></p>
  <div class="toolbar">
    <button class="btn" id="u-ft" aria-pressed="true">📏 feet</button>
    <button class="btn" id="u-m" aria-pressed="false">📐 metres</button>
    <button class="btn" id="theme">🌓 light / dark</button>
  </div>
  %%DOWNLOADS%%
</div>
<svg class="waves" viewBox="0 0 1200 60" preserveAspectRatio="none" aria-hidden="true">
<path d="M0,30 C150,60 300,0 450,30 C600,60 750,0 900,30 C1050,60 1150,15 1200,30 L1200,60 L0,60 Z" fill="var(--bg)"/></svg>
</header>
<div class="wrap">
  <section class="now" style="margin-top:-44px">
    <div class="grid tiles">%%NOW_TILES%%</div>
    <p class="lede" style="margin-top:10px">🕒 Latest reading: <b>%%LATEST%%</b>. How to read the wave vibe:</p>
    <div class="chips">%%VIBES%%</div>
    %%STALE%%
  </section>

  <section><h2>📊 The season so far</h2><p class="lede">Big-picture numbers since the buoy went in the water.</p>
    <div class="grid tiles">%%SEASON_TILES%%</div></section>

  <section><h2>💡 What the data says</h2><p class="lede">Written automatically from the numbers — it changes as new data arrives.</p>
    <div class="grid insights">%%CARDS%%</div></section>

  <section><h2>📈 Every wave, every hour</h2>
    <p class="lede">Drag the slider underneath to zoom. Orange bands are storm events (%%STORM_DEF%%).</p>
    <div class="card"><div id="timeline" class="chart"></div></div></section>

  <section><h2>🗓️ The wave calendar</h2><p class="lede">Each square is one day. Darker blue = bigger waves. Blank squares = no data.</p>
    <div class="card"><div id="calendar" class="chart"></div></div></section>

  <section class="grid two">
    <div><h2>📅 Month by month</h2><p class="lede">Average, rough-day and biggest wave heights.</p>
      <div class="card"><div id="monthly" class="chart"></div></div></div>
    <div><h2>⚡ Wave energy delivered</h2><p class="lede">How much push the waves carried toward shore each month.</p>
      <div class="card"><div id="energy" class="chart"></div></div></div>
  </section>

  <section class="grid two">
    <div><h2>🧭 Where the waves come from</h2><p class="lede">Each wedge shows how often waves arrive from that direction.</p>
      <div class="card"><div id="rose" class="chart"></div></div></div>
    <div><h2>🎵 The ocean's playlist</h2><p class="lede">Which wave “rhythms” carry the most energy each season. Peaks on the right = long, powerful swell.</p>
      <div class="card"><div id="spectrum" class="chart"></div></div></div>
  </section>

  <section><h2>🧪 Below the surface — Smart Mooring sensors</h2>
    <p class="lede">Instruments hanging under the buoy: water temperature, dissolved oxygen, currents and more.</p>
    %%SENSORS%%</section>

  <section><h2>🌬️ Storms leave a fingerprint</h2><p class="lede">When air pressure (top) drops sharply, the waves (bottom) usually jump. That's a storm passing.</p>
    <div class="card"><div id="pressure" class="chart"></div></div></section>

  <section><h2>🌀 Storm leaderboard</h2><p class="lede">Every storm event, ranked by peak wave height.</p>
    <div class="card tablewrap"><table><thead><tr><th>Rank</th><th>Started</th><th>Lasted</th><th>Peak waves</th><th>Rhythm</th><th>From</th><th>Lowest pressure (hPa)</th><th>Energy (kWh/m)</th></tr></thead>
    <tbody>%%STORM_ROWS%%</tbody></table></div></section>

  <section><h2>🔧 Buoy health</h2><p class="lede">Battery (bottom) and the humidity inside the hull (top). Humidity creeping upward can mean moisture is getting in.</p>
    <div class="card"><div id="health" class="chart"></div></div></section>

  <section>
    <details><summary>⚠️ Data-quality notes — what was cleaned and why</summary><ul>%%QC%%</ul></details>
    <details><summary>📖 Glossary — what do these words mean?</summary><dl>
      <dt>Wave height</dt><dd>“Significant wave height” (Hs): the average height of the biggest third of waves, trough to crest. It's close to what a person on the beach would say the waves are. Single waves can be about twice as big.</dd>
      <dt>Wave rhythm / period</dt><dd>Seconds between wave crests. Short (under 6 s) = choppy local wind waves. Long (10 s+) = swell that travelled from a distant storm and packs more punch.</dd>
      <dt>Direction</dt><dd>The compass direction the waves are coming <i>from</i> (like wind). “E” means waves rolling in from the east.</dd>
      <dt>hPa</dt><dd>Hectopascals — the unit for air pressure. ~1013 is average; under ~1000 usually means a storm.</dd>
      <dt>Wave energy</dt><dd>How much energy the waves carried past each metre of coastline. 1 kWh ≈ running a microwave for an hour.</dd>
      <dt>Storm event</dt><dd>%%STORM_DEF%%.</dd>
    </dl></details>
    <details><summary>🔄 How this page updates</summary><ul>%%HOWTO%%</ul></details>
  </section>
  <footer>Generated %%GENERATED%% %%FOOTSRC%%. Data: Sofar Ocean Spotter %%SPOTTER%%, University of New England.</footer>
</div>
%%PLOTLY_TAG%%
<script>
const FIGS = %%FIGS%%;
const M2FT = 3.28084;
let unit = 'ft';
try { unit = localStorage.getItem('buoy-unit') || 'ft'; } catch (e) {}
const ORIG = JSON.parse(JSON.stringify(FIGS));
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim();}
function sub(s,u){return typeof s==='string'? s.replaceAll('UNIT',u):s;}
function scaleArr(a,k){return Array.isArray(a)? a.map(x=>Array.isArray(x)? scaleArr(x,k): (x==null? null: x*k)) : a;}
function themed(layout){
  const ink=css('--ink2'), grid=css('--grid');
  const L = Object.assign({}, layout, {paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    font:{family:'system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif', color:ink, size:13},
    margin:layout.margin||{l:60,r:20,t:30,b:50}, hoverlabel:{font:{size:13}}});
  for (const k of Object.keys(L)) if (/^[xy]axis\d*$/.test(k)) L[k]=Object.assign({gridcolor:grid, zerolinecolor:grid, linecolor:grid}, L[k]);
  if (!L.xaxis) L.xaxis={gridcolor:grid}; if(!L.yaxis) L.yaxis={gridcolor:grid};
  if (L.polar){L.polar=Object.assign({bgcolor:'rgba(0,0,0,0)'},L.polar);
    L.polar.angularaxis=Object.assign({gridcolor:grid,linecolor:grid},L.polar.angularaxis);
    L.polar.radialaxis=Object.assign({gridcolor:grid,linecolor:grid},L.polar.radialaxis);}
  if (L.xaxis && L.xaxis.rangeselector) L.xaxis.rangeselector=Object.assign({bgcolor:css('--chip'),activecolor:css('--chip2'),font:{color:ink}},L.xaxis.rangeselector);
  return L;
}
function render(){
  const k = unit==='ft'? M2FT:1;
  for (const id of Object.keys(ORIG)){
    const el=document.getElementById(id); if(!el) continue;
    const f=JSON.parse(JSON.stringify(ORIG[id]));
    for (const t of f.data){
      if (t.meta==='len'){ if(t.y && t.type!=='heatmap') t.y=scaleArr(t.y,k); if(t.type==='heatmap'){t.z=scaleArr(t.z,k); t.zmax=t.zmax*k;} }
      t.hovertemplate=sub(t.hovertemplate,unit);
      if (t.colorbar&&t.colorbar.title) t.colorbar.title.text=sub(t.colorbar.title.text,unit);
    }
    const L=JSON.parse(sub(JSON.stringify(f.layout),unit));
    Plotly.react(el,f.data,themed(L),{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']});
  }
  document.querySelectorAll('.len').forEach(el=>{const m=parseFloat(el.dataset.m); if(isNaN(m))return;
    const v = unit==='ft'? m*M2FT : m; const lt = el.dataset.lt? '<':'';
    el.textContent = lt + (unit==='ft'? v.toFixed(el.dataset.lt?0:1)+' ft' : v.toFixed(el.dataset.lt?1:2)+' m');});
  document.getElementById('u-ft').setAttribute('aria-pressed', unit==='ft');
  document.getElementById('u-m').setAttribute('aria-pressed', unit==='m');
}
document.getElementById('u-ft').onclick=()=>{unit='ft'; try{localStorage.setItem('buoy-unit',unit)}catch(e){} render();};
document.getElementById('u-m').onclick=()=>{unit='m'; try{localStorage.setItem('buoy-unit',unit)}catch(e){} render();};
document.getElementById('theme').onclick=()=>{const r=document.documentElement;
  const dark = r.dataset.theme? r.dataset.theme==='dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  r.dataset.theme = dark? 'light':'dark'; try{localStorage.setItem('buoy-theme',r.dataset.theme)}catch(e){} render();};
try{const t=localStorage.getItem('buoy-theme'); if(t) document.documentElement.dataset.theme=t;}catch(e){}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',render);
render();
</script>
</body></html>
"""
