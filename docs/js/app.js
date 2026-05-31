// emergency-ai — application controller. Wires the offline engine + visual effects to the
// full feature set: voice, geo, triage, guided mode, metronome, siren, beacon, share, alert,
// medical-ID, incident log, command palette, translation, tools.
import EmergencyEngine from './engine.js';
import * as fx from './effects.js';

const $ = id => document.getElementById(id);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const LS = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem('eai.' + k)) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem('eai.' + k, JSON.stringify(v)); } catch {} },
};

const state = {
  engine: new EmergencyEngine(),
  city: null,
  lang: LS.get('lang', 'en'),
  last: null,
  abort: null,
  guided: { steps: [], why: [], i: 0, bpm: null },
};

// ───────────────────────── tiny utils (haptics + audio) ─────────────────────────
const haptic = p => { try { navigator.vibrate?.(p); } catch {} };
let _ac;
const audio = () => (_ac ||= new (window.AudioContext || window.webkitAudioContext)());
function blip(freq = 880, dur = 0.06, type = 'sine', gain = 0.18) {
  try {
    const ac = audio(), o = ac.createOscillator(), g = ac.createGain();
    o.type = type; o.frequency.value = freq; g.gain.value = gain;
    o.connect(g); g.connect(ac.destination);
    o.start(); g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + dur);
    o.stop(ac.currentTime + dur);
  } catch {}
}

// ───────────────────────── boot ─────────────────────────
init();
async function init() {
  await state.engine.load();
  buildCitySelect();
  buildScenarioGrid();
  buildLangMenu();
  applyI18n();
  fx.renderHeatmap(state.engine.cities, state.engine.cacheState());
  wireGlobal();
  wireHero();
  wireActionBar();
  wireTools();
  wirePalette();
  updateNetBadge();
  window.addEventListener('online', updateNetBadge);
  window.addEventListener('offline', updateNetBadge);
  // restore city
  const saved = LS.get('city', null);
  state.city = (saved && state.engine.resolveCity(saved)) || state.engine.resolveCity('New York') || state.engine.cities[0];
  if (state.city) $('city-select').value = state.city.slug;
}

function updateNetBadge() {
  const b = $('net-badge');
  const live = state.engine.base && state.engine.live;
  b.dataset.mode = live ? 'live' : 'offline';
  b.querySelector('.badge-label').textContent = live ? 'live API' : (navigator.onLine ? 'offline-ready' : 'offline');
}

// ───────────────────────── builders ─────────────────────────
function buildCitySelect() {
  const sel = $('city-select');
  sel.innerHTML = '';
  for (const c of state.engine.cities) {
    const o = document.createElement('option');
    o.value = c.slug; o.textContent = `${c.flag || ''} ${c.display_name}`.trim();
    sel.appendChild(o);
  }
  sel.onchange = () => { state.city = state.engine.resolveCity(sel.value); LS.set('city', sel.value); };
}

function buildScenarioGrid() {
  const grid = $('scenario-grid');
  grid.innerHTML = '';
  const featured = state.engine.scenarios.slice(0, 12);
  for (const sc of featured) {
    const b = document.createElement('button');
    b.className = 'scn'; b.dataset.u = sc.urgency;
    b.innerHTML = `<span class="scn-ico">${sc.icon || '🚑'}</span><span class="scn-lbl">${sc.short || sc.title}</span>`;
    b.onclick = () => { haptic(8); runScenario(sc); };
    grid.appendChild(b);
  }
}

function buildLangMenu() {
  $('lang-select').onclick = () => openLangSheet();
  const langs = state.engine.languages;
  $('lang-flag').textContent = langs[state.lang]?.flag || '🌐';
}

function applyI18n() {
  const lang = state.lang;
  const dir = state.engine.languages[lang]?.dir || 'ltr';
  document.documentElement.lang = lang;
  document.documentElement.dir = dir;
  $$('[data-i18n]').forEach(el => { const v = state.engine.t(el.dataset.i18n, lang); if (v) el.textContent = v; });
  $$('[data-i18n-ph]').forEach(el => { const v = state.engine.t(el.dataset.i18nPh, lang); if (v) el.placeholder = v; });
  $$('[data-i18n-title]').forEach(el => { const v = state.engine.t(el.dataset.i18nTitle, lang); if (v) el.title = v; });
  const disc = state.engine.t('disclaimer', lang); if (disc) $('resp-disclaimer').textContent = disc;
}

// ───────────────────────── query flow ─────────────────────────
async function runQuery(text) {
  if (!text || !text.trim()) return;
  const city = state.city;
  state.abort?.abort();
  state.abort = new AbortController();

  const panel = $('response'); panel.hidden = false;
  fx.showHUD();
  fx.showSkeleton($('resp-actions'), 3);
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // pre-classify for instant ambient theme (before tokens arrive)
  const pre = state.engine.classify(text);
  fx.setUrgency(pre.urgency);
  if (pre.scenario?.triage?.length) {
    const adj = await askTriage(pre.scenario);
    if (adj?.set_urgency) fx.setUrgency(adj.set_urgency);
  }

  const liEls = [];
  let finalResp = null;
  await state.engine.respond(text, city, {
    signal: state.abort.signal,
    lang: state.lang,
    onLatency: m => { if (m.ttft_ms != null) fx.updateGauge(m.ttft_ms); fx.setHudMeta(m); },
    onField: (field, value) => {
      if (field === 'urgency') { fx.setUrgency(value); setUrgencyBanner(value); }
      else if (field === 'time_to_act_seconds') startTimer(value);
      else if (field === 'immediate_actions') { $('resp-actions').innerHTML = ''; }
      else if (field === 'action_done') { const li = liEls[value.idx]; if (li) li.dataset.why = value.why || ''; }
      else if (field === 'who_to_call') renderCalls(value);
      else if (field === 'avoid') renderAvoid(value);
      else if (field === 'jurisdictional_notes') renderNotes(value);
      else if (field === 'confidence') $('resp-confidence').style.width = Math.round(value * 100) + '%';
    },
    onToken: (field, { idx, chunk }) => {
      let li = liEls[idx];
      if (!li) {
        li = document.createElement('li');
        li.innerHTML = '<span class="li-text"></span><div class="why"></div>';
        li.onclick = () => { li.classList.toggle('done'); haptic(6); };
        $('resp-actions').appendChild(li); liEls[idx] = li;
      }
      fx.revealToken(li, chunk);
    },
    onFinal: resp => {
      finalResp = resp; state.last = resp;
      // attach reasoning into .why nodes
      (resp.reasoning || []).forEach((why, i) => { const li = liEls[i]; if (li) li.querySelector('.why').textContent = why; });
      $('resp-confidence').style.width = Math.round((resp.confidence || 0) * 100) + '%';
      fx.warmChip(resp._meta.city_slug);
      recordIncident(resp, city);
      maybeAutoMetronome(resp);
    },
  });
  haptic([10, 40, 10]);
}

function runScenario(sc) {
  $('ask-input').value = sc.title;
  runQuery(sc.title);
}

// ───────────────────────── response rendering ─────────────────────────
function setUrgencyBanner(u) {
  const el = $('resp-urgency'); el.querySelector('.u-word').textContent = (state.engine.t('urgency', state.lang) ? '' : '') + u.toUpperCase();
}
let timerInt;
function startTimer(secs) {
  clearInterval(timerInt);
  const box = $('resp-timer'); box.hidden = false;
  let s = secs;
  const num = box.querySelector('.t-num');
  num.textContent = s;
  if (secs > 300) { box.hidden = true; return; }
  timerInt = setInterval(() => { s = Math.max(0, s - 1); num.textContent = s; if (s <= 0) clearInterval(timerInt); }, 1000);
}
function renderCalls(calls) {
  const box = $('resp-calls'); box.innerHTML = '';
  const labels = { primary: state.engine.t('call_now', state.lang) || 'Call now' };
  let first = true;
  for (const [k, num] of Object.entries(calls || {})) {
    const a = document.createElement('a');
    a.className = 'call-btn' + (first ? '' : ' secondary');
    a.href = `tel:${String(num).replace(/[^+\d]/g, '')}`;
    a.innerHTML = `📞 ${num}<small>${k === 'primary' ? (labels.primary) : k.replace(/_/g, ' ')}</small>`;
    a.onclick = () => haptic(20);
    box.appendChild(a); first = false;
  }
}
function renderAvoid(list) {
  const ul = $('resp-avoid'); ul.innerHTML = '';
  $('avoid-block').hidden = !(list && list.length);
  (list || []).forEach(x => { const li = document.createElement('li'); li.textContent = x; ul.appendChild(li); });
}
function renderNotes(text) {
  $('notes-block').hidden = !text;
  $('resp-notes').textContent = text || '';
}

// ───────────────────────── triage modal ─────────────────────────
function askTriage(sc) {
  return new Promise(resolve => {
    const q = sc.triage[0];
    if (!q) return resolve(null);
    const modal = $('triage-modal'), opts = $('triage-opts');
    $('triage-q').textContent = q.q;
    opts.innerHTML = '';
    q.options.forEach(opt => {
      const b = document.createElement('button');
      b.className = 'triage-opt'; b.textContent = opt.label;
      b.onclick = () => { haptic(8); close(); resolve(opt.effect || {}); };
      opts.appendChild(b);
    });
    $('triage-skip').onclick = () => { close(); resolve(null); };
    modal.hidden = false;
    function close() { modal.hidden = true; }
  });
}

// ───────────────────────── hero: orb + ask + voice + geo ─────────────────────────
function wireHero() {
  $('ask-input').addEventListener('keydown', e => { if (e.key === 'Enter') runQuery($('ask-input').value); });
  $('locate-btn').onclick = locateMe;
  $('btn-listen').onclick = voiceInput;

  // long-press SOS orb
  const orb = $('sos-orb'), ring = $('orb-progress');
  const CIRC = 339; let raf, start, fired;
  const begin = e => {
    e.preventDefault(); fired = false; start = performance.now(); orb.classList.add('charging'); haptic(10);
    const tick = now => {
      const p = Math.min(1, (now - start) / 900);
      ring.style.strokeDashoffset = String(CIRC * (1 - p));
      if (p >= 1 && !fired) { fired = true; fireSOS(); }
      else if (!fired) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  };
  const end = () => { cancelAnimationFrame(raf); orb.classList.remove('charging'); if (!fired) ring.style.strokeDashoffset = String(CIRC); };
  orb.addEventListener('pointerdown', begin);
  orb.addEventListener('pointerup', end);
  orb.addEventListener('pointerleave', end);
  orb.addEventListener('pointercancel', end);
}
function fireSOS() {
  haptic([30, 30, 30]); blip(660, 0.12, 'square');
  $('sos-orb').classList.add('fired');
  setTimeout(() => $('sos-orb').classList.remove('fired'), 500);
  const text = $('ask-input').value.trim() || 'Unknown emergency — person needs help, situation unclear';
  runQuery(text);
}

function voiceInput() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { fx.toast('Voice input not supported in this browser'); return; }
  const rec = new SR();
  rec.lang = state.lang === 'en' ? 'en-US' : state.lang;
  rec.interimResults = true; rec.continuous = false;
  const btn = $('btn-listen'); btn.classList.add('recording'); haptic(12);
  let txt = '';
  rec.onresult = e => { txt = Array.from(e.results).map(r => r[0].transcript).join(' '); $('ask-input').value = txt; };
  rec.onerror = () => { btn.classList.remove('recording'); };
  rec.onend = () => { btn.classList.remove('recording'); if (txt.trim()) runQuery(txt); };
  rec.start();
}

function locateMe() {
  if (!navigator.geolocation) { fx.toast('Geolocation unavailable'); return; }
  fx.toast('Locating…', 1500);
  navigator.geolocation.getCurrentPosition(
    pos => {
      const c = state.engine.nearestCity(pos.coords.latitude, pos.coords.longitude);
      state.city = c; $('city-select').value = c.slug; LS.set('city', c.slug);
      fx.toast(`Nearest jurisdiction: ${c.flag || ''} ${c.display_name}`); haptic(10);
    },
    () => fx.toast('Location permission denied — pick a city manually'),
    { enableHighAccuracy: false, timeout: 6000 }
  );
}

// ───────────────────────── action bar ─────────────────────────
function wireActionBar() {
  $('btn-call').onclick = () => { const n = state.last?.who_to_call?.primary || state.city?.primary || '911'; location.href = `tel:${String(n).replace(/[^+\d]/g, '')}`; };
  $('btn-speak').onclick = speakResponse;
  $('btn-guided').onclick = openGuided;
  $('btn-metronome').onclick = toggleMetronome;
  $('btn-siren').onclick = toggleSiren;
  $('btn-beacon').onclick = toggleBeacon;
  $('btn-share').onclick = shareLocation;
  $('btn-alert').onclick = alertContact;
  $('btn-export').onclick = exportReport;
  $('btn-explain').onclick = () => {
    const on = $('resp-actions').classList.toggle('show-why');
    $('btn-explain').setAttribute('aria-pressed', String(on));
  };
}

// A2/B... TTS read-out
let speaking = false;
function speakResponse() {
  if (!('speechSynthesis' in window) || !state.last) { fx.toast('Speech not supported'); return; }
  if (speaking) { speechSynthesis.cancel(); speaking = false; $('btn-speak').classList.remove('active'); return; }
  const r = state.last; const lang = state.lang === 'en' ? 'en-US' : state.lang;
  const lines = [`${r.urgency} priority.`, ...r.immediate_actions.map((a, i) => `Step ${i + 1}. ${a}`)];
  speaking = true; $('btn-speak').classList.add('active');
  lines.forEach((t, i) => {
    const u = new SpeechSynthesisUtterance(t); u.lang = lang; u.rate = 1.02;
    if (i === lines.length - 1) u.onend = () => { speaking = false; $('btn-speak').classList.remove('active'); };
    speechSynthesis.speak(u);
  });
}

// A3 — CPR metronome (110 bpm) + haptic + visual
let metroInt = null;
function toggleMetronome() {
  const btn = $('btn-metronome');
  if (metroInt) { clearInterval(metroInt); metroInt = null; btn.classList.remove('active'); $('guided-metro').hidden = true; return; }
  btn.classList.add('active');
  const period = 60000 / 110;
  const tick = () => { blip(1000, 0.04, 'square', 0.2); haptic(15); pulseMetroDot(); };
  tick(); metroInt = setInterval(tick, period);
  fx.toast('CPR metronome · push hard & fast, 110/min');
}
function pulseMetroDot() { const d = document.querySelector('.metro-dot'); if (d) { d.classList.remove('beat'); void d.offsetWidth; d.classList.add('beat'); } }
function maybeAutoMetronome(resp) {
  const sc = state.engine.scenarios.find(s => s.id === resp.scenario_id);
  if (sc?.metronome_bpm && !metroInt) fx.toast('Tap 💓 for a CPR metronome');
}

// A11 — attention siren
let siren = null;
function toggleSiren() {
  const btn = $('btn-siren');
  if (siren) { siren.stop(); siren = null; btn.classList.remove('active'); return; }
  try {
    const ac = audio(), o = ac.createOscillator(), g = ac.createGain(), lfo = ac.createOscillator(), lg = ac.createGain();
    o.type = 'sawtooth'; o.frequency.value = 900; g.gain.value = 0.25;
    lfo.frequency.value = 4; lg.gain.value = 400; lfo.connect(lg); lg.connect(o.frequency);
    o.connect(g); g.connect(ac.destination); o.start(); lfo.start();
    siren = { stop: () => { o.stop(); lfo.stop(); } };
    btn.classList.add('active'); haptic([40, 40, 40]);
  } catch { fx.toast('Audio blocked — tap again'); }
}

// A9 — strobe SOS beacon (screen + torch if available)
let beacon = null;
async function toggleBeacon() {
  const btn = $('btn-beacon');
  if (beacon) { stopBeacon(); btn.classList.remove('active'); return; }
  btn.classList.add('active');
  const ov = document.createElement('div');
  Object.assign(ov.style, { position: 'fixed', inset: '0', zIndex: '90', background: '#000', transition: 'background .05s' });
  ov.onclick = () => { stopBeacon(); btn.classList.remove('active'); };
  document.body.appendChild(ov);
  // Morse SOS: ...---...
  const SOS = [1, 1, 1, 3, 3, 3, 1, 1, 1]; const unit = 180;
  let i = 0, track = null;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    track = stream.getVideoTracks()[0];
    if (!track.getCapabilities?.().torch) { track.stop(); track = null; }
  } catch { track = null; }
  const flashOn = on => { ov.style.background = on ? '#fff' : '#000'; if (track) track.applyConstraints({ advanced: [{ torch: on }] }).catch(() => {}); haptic(on ? 8 : 0); };
  function step() {
    if (!beacon) return;
    const dur = SOS[i % SOS.length] * unit;
    flashOn(true);
    setTimeout(() => { flashOn(false); i++; beacon.t = setTimeout(step, i % SOS.length === 0 ? unit * 5 : unit); }, dur);
  }
  beacon = { ov, track, t: null };
  step();
  function _noop() {}
}
function stopBeacon() {
  if (!beacon) return;
  clearTimeout(beacon.t);
  beacon.track?.applyConstraints({ advanced: [{ torch: false }] }).catch(() => {});
  beacon.track?.stop();
  beacon.ov.remove();
  beacon = null;
}

// A8 — precise location share
function shareLocation() {
  if (!navigator.geolocation) { fx.toast('Geolocation unavailable'); return; }
  fx.toast('Getting precise location…', 1500);
  navigator.geolocation.getCurrentPosition(async pos => {
    const { latitude: la, longitude: lo, accuracy } = pos.coords;
    const link = `https://maps.google.com/?q=${la.toFixed(6)},${lo.toFixed(6)}`;
    const msg = `EMERGENCY — my location: ${la.toFixed(6)}, ${lo.toFixed(6)} (±${Math.round(accuracy)}m)\n${link}`;
    if (navigator.share) { try { await navigator.share({ title: 'My emergency location', text: msg, url: link }); return; } catch {} }
    try { await navigator.clipboard.writeText(msg); fx.toast('Location copied to clipboard'); } catch { fx.toast(link); }
  }, () => fx.toast('Location permission denied'), { enableHighAccuracy: true, timeout: 8000 });
}

// A10 — emergency contact auto-alert (prefilled SMS)
function alertContact() {
  const med = LS.get('medical', {});
  const num = med.contact_phone;
  if (!num) { fx.toast('Set an emergency contact in Medical ID first'); openSheet('medical'); return; }
  const sit = $('ask-input').value.trim() || (state.last ? `${state.last.urgency} emergency` : 'emergency');
  const finish = body => { location.href = `sms:${num}?&body=${encodeURIComponent(body)}`; };
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(pos => {
      const link = `https://maps.google.com/?q=${pos.coords.latitude.toFixed(5)},${pos.coords.longitude.toFixed(5)}`;
      finish(`I have an emergency (${sit}). My location: ${link}`);
    }, () => finish(`I have an emergency (${sit}). Please call me.`), { timeout: 5000 });
  } else finish(`I have an emergency (${sit}). Please call me.`);
}

// B5 — incident export
function exportReport() {
  const incidents = LS.get('incidents', []);
  if (!incidents.length) { fx.toast('No incidents logged yet'); return; }
  const lines = ['# emergency-ai — incident report', '', `Generated: ${new Date().toISOString()}`, '',
    '| time | city | urgency | TTFT | total | source |', '|---|---|---|---|---|---|',
    ...incidents.map(e => `| ${e.ts} | ${e.city} | ${e.urgency} | ${e.ttft_ms ?? '—'}ms | ${e.total_ms ?? '—'}ms | ${e.source} |`),
    '', '_No situation text is stored — privacy by design._'];
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = `incident-report-${Date.now()}.md`; a.click();
  fx.toast('Incident report downloaded');
}
function recordIncident(resp, city) {
  const list = LS.get('incidents', []);
  list.unshift({ ts: new Date().toISOString().slice(0, 19).replace('T', ' '), city: city?.display_name || '—',
    urgency: resp.urgency, ttft_ms: resp._meta?.ttft_ms, total_ms: resp._meta?.total_ms, source: resp._meta?.source });
  LS.set('incidents', list.slice(0, 100));
}

// A13 — guided full-screen mode
function openGuided() {
  if (!state.last) return;
  state.guided = { steps: state.last.immediate_actions, why: state.last.reasoning || [], i: 0,
    bpm: state.engine.scenarios.find(s => s.id === state.last.scenario_id)?.metronome_bpm || null };
  $('guided-total').textContent = state.guided.steps.length;
  $('guided-metro').hidden = !state.guided.bpm;
  $('guided-modal').hidden = false;
  renderGuided();
  if (state.guided.bpm && !metroInt) toggleMetronome();
}
function renderGuided() {
  const g = state.guided;
  $('guided-idx').textContent = g.i + 1;
  $('guided-text').textContent = g.steps[g.i] || '';
  const why = g.why[g.i];
  $('guided-why').hidden = !why; $('guided-why').textContent = why || '';
  $('guided-prev').style.visibility = g.i === 0 ? 'hidden' : 'visible';
  const last = g.i === g.steps.length - 1;
  $('guided-next').style.display = last ? 'none' : '';
  $('guided-done').style.display = last ? 'block' : 'none';
}

// ───────────────────────── tools / sheets ─────────────────────────
function wireTools() {
  $$('.tool').forEach(b => b.onclick = () => openSheet(b.dataset.tool));
  $('sheet-close').onclick = () => $('sheet').hidden = true;
  $('open-settings').onclick = () => openSheet('settings');
  // guided controls
  $('guided-close').onclick = () => { $('guided-modal').hidden = true; if (metroInt) toggleMetronome(); };
  $('guided-prev').onclick = () => { if (state.guided.i > 0) { state.guided.i--; renderGuided(); haptic(6); } };
  $('guided-next').onclick = () => { if (state.guided.i < state.guided.steps.length - 1) { state.guided.i++; renderGuided(); haptic(6); } };
  $('guided-done').onclick = () => { $('guided-modal').hidden = true; if (metroInt) toggleMetronome(); fx.toast('Stay with them until help arrives'); };
}

function openSheet(kind) {
  const body = $('sheet-body'); body.innerHTML = '';
  const titles = { fast: 'FAST stroke test', law: 'Jurisdiction law explorer', poison: 'Poison & overdose lookup',
    disaster: 'Disaster protocols', medical: 'Medical ID', incident: 'Incident log', settings: 'Settings' };
  $('sheet-title').textContent = titles[kind] || kind;
  ({ fast: sheetFast, law: sheetLaw, poison: sheetPoison, disaster: sheetDisaster,
     medical: sheetMedical, incident: sheetIncident, settings: sheetSettings }[kind] || (() => {}))(body);
  $('sheet').hidden = false;
}

// B1 — FAST stroke test
function sheetFast(body) {
  const ref = state.engine.data.medical_ref?.fast || { F: 'Ask them to smile — does one side droop?', A: 'Ask them to raise both arms — does one drift down?', S: 'Ask them to repeat a sentence — is speech slurred?', T: 'Time — note when symptoms started and call now.' };
  const steps = [['F', 'Face', ref.F], ['A', 'Arms', ref.A], ['S', 'Speech', ref.S]];
  let score = 0, idx = 0;
  const wrap = document.createElement('div'); wrap.className = 'field';
  body.appendChild(wrap);
  const render = () => {
    if (idx >= steps.length) {
      wrap.innerHTML = `<div class="urgency-banner" style="--accent:${score ? 'var(--u-critical)' : 'var(--u-medium)'}">${score ? 'STROKE LIKELY — CALL NOW' : 'No FAST signs — stay alert'}</div>
        <p class="law-item"><b>T — Time:</b> ${ref.T}</p>
        <button class="btn-primary" id="fast-call">📞 Call ${state.city?.primary || '911'}</button>`;
      wrap.querySelector('#fast-call').onclick = () => location.href = `tel:${(state.city?.primary || '911').replace(/[^+\d]/g, '')}`;
      return;
    }
    const [k, name, q] = steps[idx];
    wrap.innerHTML = `<label>${k} · ${name}</label><div class="triage-q" style="font-size:18px">${q}</div>
      <div class="triage-opts"><button class="triage-opt" data-v="1">Yes — sign present</button><button class="triage-opt" data-v="0">No</button></div>`;
    wrap.querySelectorAll('.triage-opt').forEach(b => b.onclick = () => { score += Number(b.dataset.v); idx++; haptic(8); render(); });
  };
  render();
}

// B4 — law explorer
function sheetLaw(body) {
  const search = document.createElement('input'); search.className = 'palette-input'; search.placeholder = 'Search laws / cities…';
  search.style.marginBottom = '12px'; body.appendChild(search);
  const list = document.createElement('div'); body.appendChild(list);
  const draw = q => {
    list.innerHTML = '';
    for (const c of state.engine.cities) {
      const laws = (c.laws || []).filter(l => !q || (l.title + l.text + c.display_name).toLowerCase().includes(q));
      if (!laws.length) continue;
      const card = document.createElement('div'); card.className = 'law-city';
      card.innerHTML = `<h4>${c.flag || ''} ${c.display_name} · <span style="color:var(--text-mut)">${c.primary}</span></h4>` +
        laws.map(l => `<div class="law-item"><b>${l.title}</b> ${l.ref ? `<span style="color:var(--text-mut)">(${l.ref})</span>` : ''}<br>${l.text}</div>`).join('');
      list.appendChild(card);
    }
  };
  search.oninput = () => draw(search.value.toLowerCase().trim());
  draw('');
}

// B6 — poison lookup
function sheetPoison(body) {
  const subs = state.engine.data.poison?.substances || [];
  const search = document.createElement('input'); search.className = 'palette-input'; search.placeholder = 'Substance…'; search.style.marginBottom = '12px';
  body.appendChild(search);
  const list = document.createElement('div'); body.appendChild(list);
  const draw = q => {
    list.innerHTML = '';
    subs.filter(s => !q || s.name.toLowerCase().includes(q)).forEach(s => {
      const c = document.createElement('div'); c.className = 'law-city';
      c.innerHTML = `<h4>☠️ ${s.name}</h4>
        <div class="law-item"><b>Induce vomiting?</b> ${s.induce_vomiting ? '<span style="color:var(--u-critical)">only if told to</span>' : '<b style="color:var(--ok)">NO</b>'}</div>
        ${(s.first_aid || []).map(f => `<div class="law-item">${f}</div>`).join('')}
        <div class="law-item"><b>Call:</b> ${s.call || '1-800-222-1222'}</div>`;
      list.appendChild(c);
    });
    if (!list.children.length) list.innerHTML = '<p class="law-item">No match. When unsure, call Poison Control: 1-800-222-1222.</p>';
  };
  search.oninput = () => draw(search.value.toLowerCase().trim()); draw('');
}

// B9 — disaster protocols
function sheetDisaster(body) {
  const protos = state.engine.data.disasters?.protocols || [];
  protos.forEach(p => {
    const c = document.createElement('div'); c.className = 'law-city';
    c.innerHTML = `<h4>${p.icon || '⚠️'} ${p.title}</h4>` +
      (p.steps || []).map((s, i) => `<div class="law-item"><b>${i + 1}.</b> ${s}</div>`).join('') +
      (p.avoid || []).map(a => `<div class="law-item" style="border-color:var(--u-critical)">✕ ${a}</div>`).join('');
    body.appendChild(c);
  });
}

// A12 — medical ID
function sheetMedical(body) {
  const m = LS.get('medical', {});
  const fields = [['name', 'Name'], ['blood', 'Blood type'], ['allergies', 'Allergies'], ['conditions', 'Conditions'], ['meds', 'Medications'], ['contact_name', 'Emergency contact name'], ['contact_phone', 'Emergency contact phone']];
  fields.forEach(([k, label]) => {
    const f = document.createElement('div'); f.className = 'field';
    f.innerHTML = `<label>${label}</label><input data-k="${k}" value="${(m[k] || '').replace(/"/g, '&quot;')}" />`;
    body.appendChild(f);
  });
  const save = document.createElement('button'); save.className = 'btn-primary'; save.textContent = 'Save medical ID';
  save.onclick = () => {
    const next = {}; body.querySelectorAll('input[data-k]').forEach(i => next[i.dataset.k] = i.value.trim());
    LS.set('medical', next); fx.toast('Medical ID saved locally (never transmitted)'); haptic(10);
  };
  body.appendChild(save);
  const note = document.createElement('p'); note.className = 'disclaimer'; note.textContent = 'Stored only on this device. Used to prefill emergency-contact alerts.';
  body.appendChild(note);
}

// B5 — incident log
function sheetIncident(body) {
  const incidents = LS.get('incidents', []);
  if (!incidents.length) { body.innerHTML = '<p class="law-item">No incidents yet. Each query is logged locally — no situation text, only city/urgency/latency.</p>'; return; }
  incidents.forEach(e => {
    const r = document.createElement('div'); r.className = 'timeline-row';
    r.innerHTML = `<time>${e.ts.slice(11)}</time><span style="flex:1">${e.city} · <b style="color:var(--accent)">${e.urgency}</b></span><span style="color:var(--text-mut)">${e.ttft_ms ?? '—'}ms ${e.source}</span>`;
    body.appendChild(r);
  });
  const exp = document.createElement('button'); exp.className = 'btn-primary'; exp.textContent = '📄 Export report'; exp.onclick = exportReport;
  body.appendChild(exp);
}

function sheetSettings(body) {
  const base = LS.get('apibase', '');
  body.innerHTML = `<div class="field"><label>Live API base URL (optional)</label><input id="set-api" placeholder="https://emergency-ai.fly.dev" value="${base}" /></div>
    <p class="disclaimer">Leave blank to run fully offline. When set & reachable, responses stream from the live Haiku-backed service.</p>`;
  const save = document.createElement('button'); save.className = 'btn-primary'; save.textContent = 'Save';
  save.onclick = () => { const v = $('set-api').value.trim(); LS.set('apibase', v); window.EMERGENCY_API_BASE = v || null; state.engine.base = v || null; if (v) state.engine._probeLive().then(updateNetBadge); else { state.engine.live = false; updateNetBadge(); } fx.toast('Saved'); };
  body.appendChild(save);
  const clear = document.createElement('button'); clear.className = 'ghost-btn'; clear.style.marginTop = '10px'; clear.textContent = 'Clear all local data';
  clear.onclick = () => { ['city', 'lang', 'medical', 'incidents', 'apibase'].forEach(k => localStorage.removeItem('eai.' + k)); fx.toast('Cleared'); };
  body.appendChild(clear);
}

function openLangSheet() {
  const body = $('sheet-body'); body.innerHTML = ''; $('sheet-title').textContent = 'Language';
  const langs = state.engine.languages;
  Object.entries(langs).forEach(([code, info]) => {
    const b = document.createElement('button'); b.className = 'triage-opt'; b.style.marginBottom = '8px';
    b.textContent = `${info.flag || ''} ${info.name}`;
    b.onclick = () => { state.lang = code; LS.set('lang', code); $('lang-flag').textContent = info.flag || '🌐'; applyI18n(); $('sheet').hidden = true; haptic(8); };
    body.appendChild(b);
  });
  $('sheet').hidden = false;
}

// ───────────────────────── command palette (⌘K) ─────────────────────────
function wirePalette() {
  $('open-palette').onclick = openPalette;
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    if (e.key === 'Escape') { $('palette').hidden = true; $('sheet').hidden = true; }
  });
  $('palette-input').addEventListener('input', drawPalette);
  $('palette-input').addEventListener('keydown', e => {
    const items = $$('.pal-item'); const sel = items.findIndex(i => i.classList.contains('sel'));
    if (e.key === 'ArrowDown') { e.preventDefault(); move(items, sel, 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(items, sel, -1); }
    else if (e.key === 'Enter') { (items[Math.max(0, sel)] || items[0])?.click(); }
  });
}
function move(items, sel, d) { items.forEach(i => i.classList.remove('sel')); const n = ((sel < 0 ? 0 : sel) + d + items.length) % items.length; items[n]?.classList.add('sel'); items[n]?.scrollIntoView({ block: 'nearest' }); }
function openPalette() { $('palette').hidden = false; const i = $('palette-input'); i.value = ''; drawPalette(); i.focus(); }
function drawPalette() {
  const q = $('palette-input').value.toLowerCase().trim();
  const items = [
    ...state.engine.scenarios.map(s => ({ ico: s.icon, label: s.title, kind: 'scenario', run: () => runScenario(s) })),
    ...state.engine.cities.map(c => ({ ico: c.flag || '🏙️', label: c.display_name, kind: 'city', run: () => { state.city = c; $('city-select').value = c.slug; LS.set('city', c.slug); fx.toast(c.display_name); } })),
    { ico: '🧠', label: 'FAST stroke test', kind: 'tool', run: () => openSheet('fast') },
    { ico: '⚖️', label: 'Law explorer', kind: 'tool', run: () => openSheet('law') },
    { ico: '☠️', label: 'Poison lookup', kind: 'tool', run: () => openSheet('poison') },
    { ico: '🪪', label: 'Medical ID', kind: 'tool', run: () => openSheet('medical') },
  ].filter(it => !q || it.label.toLowerCase().includes(q));
  const box = $('palette-results'); box.innerHTML = '';
  items.slice(0, 30).forEach((it, i) => {
    const el = document.createElement('div'); el.className = 'pal-item' + (i === 0 ? ' sel' : '');
    el.innerHTML = `<span class="pal-ico">${it.ico || '•'}</span><span>${it.label}</span><span class="pal-kind">${it.kind}</span>`;
    el.onclick = () => { $('palette').hidden = true; it.run(); };
    box.appendChild(el);
  });
}

// ───────────────────────── global wiring ─────────────────────────
function wireGlobal() {
  // close overlays on backdrop click
  ['triage-modal', 'palette'].forEach(id => $(id).addEventListener('click', e => { if (e.target.id === id) $(id).hidden = true; }));
  $('sheet').addEventListener('click', e => { if (e.target.id === 'sheet') $('sheet').hidden = true; });
  // restore api base
  const base = LS.get('apibase', ''); if (base) { window.EMERGENCY_API_BASE = base; state.engine.base = base; state.engine._probeLive().then(updateNetBadge); }
  // parallax on the response card
  fx.initParallax($('response'));
}
