"""
mapjs.py — the "Where is the buoy?" map, shared by the wave-buoy and BlueSonde dashboards.
Leaflet map with drawn buoy icons (Spotter = yellow wave buoy, BlueSonde = orange water-quality buoy),
labels that stay readable on both the street map and the NOAA nautical chart.
Same file in every UNE buoy repo — edit it in one place and copy it to the others.
"""
import json

HEAD = ('<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>')

CSS = r"""
#map{height:100%;min-height:380px;background:var(--chip);z-index:0}
.buoy-ic{background:none;border:0}
.buoy-ic svg{display:block;filter:drop-shadow(0 1px 1.5px rgba(0,0,0,.45))}
.buoy-ic.me svg{animation:bob 3s ease-in-out infinite}
.buoy-ic.me .ring{position:absolute;left:50%;top:62%;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;
  border:2px solid #eb6834;animation:ping 2.4s ease-out infinite;pointer-events:none}
@keyframes bob{0%,100%{transform:translateY(0) rotate(-3deg)}50%{transform:translateY(-2px) rotate(3deg)}}
@keyframes ping{0%{transform:scale(.4);opacity:.9}100%{transform:scale(1.5);opacity:0}}
@media (prefers-reduced-motion:reduce){.buoy-ic.me svg,.buoy-ic.me .ring{animation:none}}
.leaflet-tooltip.blabel{background:rgba(255,255,255,.94);color:#16181a;border:1px solid rgba(0,0,0,.18);border-radius:7px;
  padding:1px 7px;font:600 12px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.leaflet-tooltip.blabel.me{background:#eb6834;color:#fff;border-color:#b8471d;font-size:13px}
.leaflet-tooltip.blabel:before{display:none}
.leaflet-popup-content{font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:10px 14px}
.leaflet-popup-content b{font-size:15px}
.maplegend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:13px;color:var(--ink2);padding:6px 12px 0}
.maplegend span{display:inline-flex;align-items:center;gap:5px}
"""

JS = r"""
const MAPDATA = %%MAPDATA%%;
const BUOY_SVG = {
  wave: s => `<svg width="${s}" height="${s*1.15}" viewBox="0 0 40 46" aria-hidden="true">
    <ellipse cx="20" cy="40" rx="17" ry="4.5" fill="#1c5cab" opacity=".28"/>
    <line x1="20" y1="13" x2="20" y2="2" stroke="#222" stroke-width="1.6"/><circle cx="20" cy="2.5" r="1.6" fill="#222"/>
    <path d="M4 25a16 16 0 0 0 32 0z" fill="#ffc915" stroke="#7a5d00" stroke-width="1.2"/>
    <path d="M4 25a16 13 0 0 1 32 0z" fill="#ffc915" stroke="#7a5d00" stroke-width="1.2"/>
    <path d="M8.5 21.5a12 9 0 0 1 23 0l-2 3h-19z" fill="#1f2a3a"/>
    <path d="M12 20.5h16M14 17.5h12" stroke="#5b8fd1" stroke-width=".9"/>
    <path d="M4 25h32" stroke="#7a5d00" stroke-width="1.2"/></svg>`,
  wq: s => `<svg width="${s}" height="${s*1.15}" viewBox="0 0 40 46" aria-hidden="true">
    <ellipse cx="20" cy="40" rx="15" ry="4" fill="#1c5cab" opacity=".28"/>
    <rect x="18.2" y="30" width="3.6" height="13" rx="1" fill="#8a96a3" stroke="#4d5966" stroke-width=".8"/>
    <line x1="27" y1="10" x2="27" y2="1.5" stroke="#222" stroke-width="1.5"/><circle cx="27" cy="2" r="1.5" fill="#222"/>
    <rect x="6" y="18" width="28" height="13" rx="6.5" fill="#ff7a1a" stroke="#9c3d00" stroke-width="1.2"/>
    <rect x="6" y="23.5" width="28" height="2.4" fill="#fff" opacity=".85"/>
    <path d="M10 18l3-7h14l3 7z" fill="#1f2a3a" stroke="#0c1118" stroke-width=".8"/>
    <path d="M14.5 14.5h11" stroke="#5b8fd1" stroke-width=".9"/></svg>`};
let LMAP=null, LBASE=null, LBASE_KEY='';
function buoyIcon(kind, me){
  const s = me? 40 : 28;
  return L.divIcon({className:'buoy-ic'+(me?' me':''), html:(me?'<div class="ring"></div>':'')+BUOY_SVG[kind||'wq'](s),
    iconSize:[s, s*1.15], iconAnchor:[s/2, s*0.87], tooltipAnchor:[0, -s*0.8], popupAnchor:[0, -s*0.75]});
}
function mapIsDark(){ return getComputedStyle(document.body).backgroundColor.match(/\d+/g).slice(0,3).reduce((a,c)=>a+ +c,0) < 300; }
function esc(t){ return String(t).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function renderMap(){
  const el = document.getElementById('map'); if (!el || typeof L === 'undefined') return;
  if (!LMAP){
    LMAP = L.map(el, {scrollWheelZoom:false, zoomControl:true, attributionControl:true}).setView([MAPDATA.center[0], MAPDATA.center[1]], MAPDATA.zoom);
    el.addEventListener('click', ()=>LMAP.scrollWheelZoom.enable(), {once:true});
    if (MAPDATA.track && MAPDATA.track.length > 1)
      L.polyline(MAPDATA.track, {color:'#2a78d6', weight:2.5, opacity:.85}).addTo(LMAP).bindTooltip('Last 7 days of drift around the anchor');
    if (MAPDATA.anchor)
      L.marker(MAPDATA.anchor, {icon:L.divIcon({className:'buoy-ic', html:'<div style="font-size:16px;line-height:16px">⚓</div>', iconSize:[16,16], iconAnchor:[8,8]})})
        .addTo(LMAP).bindTooltip('Mooring (anchor)');
    for (const o of (MAPDATA.others||[])){
      L.marker([o.lat, o.lon], {icon:buoyIcon(o.kind, false), title:o.label}).addTo(LMAP)
        .bindTooltip(esc(o.label), {permanent:true, direction:'right', offset:[4,-12], className:'blabel'})
        .bindPopup(`<b>${esc(o.label)}</b><br>${o.kind==='wave'?'🌊 Wave buoy (Sofar Spotter)':'🧪 Water-quality buoy (BlueSonde)'}<br><a href="${esc(o.url)}">Open its dashboard →</a>`);
    }
    const me = MAPDATA.me;
    L.marker([me.lat, me.lon], {icon:buoyIcon(me.kind, true), zIndexOffset:1000, title:me.label}).addTo(LMAP)
      .bindTooltip(esc(me.label), {permanent:true, direction:'top', className:'blabel me'})
      .bindPopup(`<b>${esc(me.label)}</b><br>${esc(me.sub||'')}`);
    const pts = [[me.lat, me.lon]].concat((MAPDATA.others||[]).map(o=>[o.lat, o.lon])).concat(MAPDATA.track||[]);
    if (pts.length > 1) LMAP.fitBounds(pts, {padding:[40, 60], maxZoom:15});
  }
  const key = mapMode + '|' + (mapMode==='chart' ? unitKey() : (mapIsDark()?'dark':'light'));
  if (key !== LBASE_KEY){
    if (LBASE) LMAP.removeLayer(LBASE);
    if (mapMode==='chart'){
      LBASE = L.tileLayer.wms('https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/MaritimeChartService/WMSServer', {
        layers:'0,1,2,3,4,5,6,7', format:'image/png', transparent:false, version:'1.3.0', maxZoom:18,
        display_params: JSON.stringify({ECDISParameters:{DynamicParameters:{Parameter:[{name:'DisplayDepthUnits',value: unitKey()==='metric'?1:2}]}}}),
        attribution:'NOAA Office of Coast Survey — not for navigation'});
    } else {
      LBASE = (mapIsDark()
        ? L.layerGroup([L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {maxZoom:16, attribution:'Esri, HERE, Garmin, &copy; OpenStreetMap contributors'}),
                        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {maxZoom:16})])
        : L.layerGroup([L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}', {maxZoom:16, attribution:'Esri, GEBCO, NOAA, Garmin, HERE, &copy; OpenStreetMap contributors'}),
                        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}', {maxZoom:16})]));
    }
    LBASE.addTo(LMAP); LBASE_KEY = key;
  }
  setTimeout(()=>LMAP.invalidateSize(), 50);
}
"""

LEGEND = ('<div class="maplegend"><span><svg width="18" height="20" viewBox="0 0 40 46"><path d="M4 25a16 16 0 0 0 32 0z" fill="#ffc915" stroke="#7a5d00"/>'
          '<path d="M4 25a16 13 0 0 1 32 0z" fill="#1f2a3a"/></svg>Wave buoy</span>'
          '<span><svg width="18" height="20" viewBox="0 0 40 46"><rect x="6" y="18" width="28" height="13" rx="6.5" fill="#ff7a1a" stroke="#9c3d00"/>'
          '<path d="M10 18l3-7h14l3 7z" fill="#1f2a3a"/></svg>Water-quality buoy</span>'
          '<span>Click a buoy to visit its page</span></div>')


def kind_of(site):
    return site.get('kind') or ('wave' if 'SPOT' in site.get('url', '') else 'wq')


def mapdata(me, others, center=None, zoom=12, track=None, anchor=None):
    return json.dumps(dict(me=me, others=[dict(o, kind=kind_of(o)) for o in others if o.get('lat') and o.get('lon')],
                           center=center or [me['lat'], me['lon']], zoom=zoom, track=track or [], anchor=anchor),
                      ensure_ascii=False)
