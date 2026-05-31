// emergency-ai — offline inference engine + live-API bridge.
// Dual path: if window.EMERGENCY_API_BASE is set, stream from the real FastAPI service;
// otherwise run a deterministic, fully-offline triage + scenario engine that *mimics*
// streaming inference so the UX is identical with zero network.

const DATA = ['scenarios', 'cities', 'i18n', 'poison', 'disasters', 'medical_ref'];

// Weighted keyword lexicon — mirror of src/emergency_ai/core/triage.py
const LEX = {
  critical: [
    ['not breathing', 5], ['no pulse', 5], ['cardiac arrest', 5], ['stopped breathing', 5],
    ['unconscious', 4], ['unresponsive', 4], ['choking', 4], ['can\'t breathe', 4], ['cant breathe', 4],
    ['severe bleeding', 4], ['bleeding badly', 4], ['gushing', 4], ['spurting', 4], ['overdose', 4],
    ['anaphyla', 4], ['drowning', 4], ['not moving', 3], ['turning blue', 4], ['collapsed', 3],
    ['stroke', 4], ['seizure', 3], ['convuls', 3], ['electrocut', 4], ['gunshot', 5], ['stabbed', 5],
  ],
  high: [
    ['chest pain', 3], ['heart attack', 4], ['can\'t move', 2], ['broken', 2], ['fracture', 2],
    ['burn', 2], ['head injury', 3], ['concuss', 2], ['allergic', 2], ['epipen', 3], ['poison', 3],
    ['fall', 2], ['fell', 2], ['deep cut', 3], ['labor', 2], ['contractions', 2], ['hypothermia', 2],
    ['heat stroke', 3], ['difficulty breathing', 3], ['trouble breathing', 3], ['slurred', 3],
    ['numb', 2], ['face droop', 4], ['vomiting blood', 3],
  ],
  medium: [
    ['fever', 1], ['vomit', 1], ['dizzy', 1], ['sprain', 1], ['cut', 1], ['rash', 1],
    ['pain', 1], ['nausea', 1], ['dehydrat', 1], ['minor', 1],
  ],
};
const URGENCY_RANK = { low: 0, medium: 1, high: 2, critical: 3 };
// Down-modifiers: when present (and nothing critical fired) they cap urgency and
// suppress "severe" scenario matches, so "minor cut" never becomes "severe bleeding".
const DOWNMODS = ['minor', 'mild', 'small', 'slight', 'superficial', 'tiny', 'a little', 'just a'];

const toRad = d => (d * Math.PI) / 180;
function haversine(a, b, c, d) {
  const R = 6371, dLat = toRad(c - a), dLon = toRad(d - b);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a)) * Math.cos(toRad(c)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const sleep = ms => new Promise(r => setTimeout(r, ms));

export default class EmergencyEngine {
  constructor() {
    this.data = {};
    this.cities = [];
    this.scenarios = [];
    this._warm = new Set();      // city slugs whose prompt-cache is "warm"
    this.base = (typeof window !== 'undefined' && window.EMERGENCY_API_BASE) || null;
    this.ready = false;
  }

  async load() {
    const results = await Promise.allSettled(
      DATA.map(name => fetch(`data/${name}.json`).then(r => (r.ok ? r.json() : Promise.reject(r.status))))
    );
    DATA.forEach((name, i) => { this.data[name] = results[i].status === 'fulfilled' ? results[i].value : null; });
    this.cities = (this.data.cities?.cities || FALLBACK_CITIES).slice().sort((a, b) => a.display_name.localeCompare(b.display_name));
    this.scenarios = this.data.scenarios?.scenarios || FALLBACK_SCENARIOS;
    this._cityBySlug = Object.fromEntries(this.cities.map(c => [c.slug, c]));
    this.ready = true;
    // probe whether a live API is actually reachable (non-blocking)
    if (this.base) this._probeLive();
    return this;
  }

  async _probeLive() {
    try {
      const r = await fetch(`${this.base}/health`, { signal: AbortSignal.timeout(1500) });
      this.live = r.ok;
    } catch { this.live = false; }
  }

  get languages() { return this.data.i18n?.languages || { en: { name: 'English', flag: '🇬🇧', dir: 'ltr' } }; }
  t(key, lang = 'en') {
    const s = this.data.i18n?.strings?.[key];
    if (!s) return null;
    return s[lang] ?? s.en ?? null;
  }

  resolveCity(nameOrSlug) {
    const n = norm(nameOrSlug).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    return this._cityBySlug[n] || this.cities.find(c => norm(c.display_name) === norm(nameOrSlug))
      || this.cities.find(c => (c.aliases || []).some(a => norm(a) === norm(nameOrSlug))) || null;
  }
  nearestCity(lat, lon) {
    let best = this.cities[0], bestD = Infinity;
    for (const c of this.cities) {
      if (c.lat == null || c.lon == null) continue;
      const dKm = haversine(lat, lon, c.lat, c.lon);
      if (dKm < bestD) { bestD = dKm; best = c; }
    }
    return best;
  }
  cacheState() { return Object.fromEntries(this.cities.map(c => [c.slug, this._warm.has(c.slug) ? 'warm' : 'cold'])); }

  // Transparent weighted-keyword triage. Returns urgency + matched scenario + signals.
  classify(text) {
    const t = norm(text);
    const signals = [];
    const score = { low: 0, medium: 0, high: 0, critical: 0 };
    for (const [level, terms] of Object.entries(LEX)) {
      for (const [term, w] of terms) {
        if (t.includes(term)) { score[level] += w; signals.push(term); }
      }
    }
    const downplayed = score.critical === 0 && DOWNMODS.some(d => t.includes(d));

    // lexicon urgency = highest tier with any signal
    let urgency = 'medium', total = 0;
    for (const lvl of ['critical', 'high', 'medium', 'low']) {
      if (score[lvl] > 0) { urgency = lvl; total = score[lvl]; break; }
    }
    if (!total) urgency = downplayed ? 'low' : 'high'; // no signal: minor→low, else err upward

    // best scenario by keyword overlap; a downplayed report won't match a "severe" scenario
    let scenario = null, sBest = 0;
    for (const sc of this.scenarios) {
      if (downplayed && URGENCY_RANK[sc.urgency] >= URGENCY_RANK.high) continue;
      let s = 0;
      for (const kw of sc.keywords || []) if (t.includes(norm(kw))) s += 2;
      if (sc.short && t.includes(norm(sc.short))) s += 3;
      if (sc.title && t.includes(norm(sc.title))) s += 3;
      if (s > sBest) { sBest = s; scenario = sc; }
    }

    // a confident scenario match (≥1 keyword) may raise urgency; downplay caps at medium
    if (scenario && sBest >= 2 && URGENCY_RANK[scenario.urgency] > URGENCY_RANK[urgency]) urgency = scenario.urgency;
    if (downplayed && URGENCY_RANK[urgency] > URGENCY_RANK.medium) urgency = 'medium';
    return { urgency, score: total + sBest, scenario, signals: [...new Set(signals)] };
  }

  // Compose a structured response from a matched scenario (or a safe generic fallback).
  _compose(text, city, cls) {
    const sc = cls.scenario;
    const primary = city?.primary || '911';
    if (sc) {
      const calls = { ...(sc.who_to_call || {}) };
      if (!calls.primary) calls.primary = primary;
      // localise primary to the city
      calls.primary = primary;
      return {
        urgency: cls.urgency, time_to_act_seconds: sc.time_to_act_seconds ?? 60,
        immediate_actions: sc.immediate_actions || [], reasoning: sc.reasoning || [],
        avoid: sc.avoid || [], who_to_call: calls,
        jurisdictional_notes: this._notesFor(city, sc),
        confidence: 0.78 + Math.min(0.18, cls.score * 0.01),
        disclaimer: this.t('disclaimer') || DISCLAIMER, scenario_id: sc.id,
      };
    }
    return {
      urgency: cls.urgency, time_to_act_seconds: 60,
      immediate_actions: [
        `Call ${primary} now and describe what you see.`,
        'Keep the person still unless they are in immediate danger.',
        'Stay on the line and follow the operator\'s instructions.',
      ],
      reasoning: [
        'Trained dispatchers triage and route the right responders fastest.',
        'Moving an injured person can worsen spinal or internal injury.',
        'Operators give CPR/first-aid coaching in real time.',
      ],
      avoid: ['Don\'t hang up until told to.', 'Don\'t give food or water to someone who may need surgery.'],
      who_to_call: { primary }, jurisdictional_notes: this._notesFor(city, null),
      confidence: 0.4, disclaimer: this.t('disclaimer') || DISCLAIMER, scenario_id: null,
    };
  }
  _notesFor(city, sc) {
    if (!city?.laws?.length) return '';
    // surface the most relevant law (Good Samaritan / overdose amnesty) for the scenario
    const wantOverdose = sc && /overdose|opioid|drug/i.test(sc.id + ' ' + (sc.tags || []).join(' '));
    const law = city.laws.find(l => wantOverdose && /amnesty|overdose|samaritan/i.test(l.title))
      || city.laws.find(l => /samaritan/i.test(l.title)) || city.laws[0];
    return law ? `${city.display_name}: ${law.text}` : '';
  }

  /**
   * Stream a response. Calls callbacks as fields/tokens materialise.
   * opts: { onField(field,value), onToken(field,chunk), onLatency({ttft_ms,total_ms,cache_hit,source}),
   *         onFinal(resp), signal, lang }
   */
  async respond(text, city, opts = {}) {
    const { onField, onToken, onLatency, onFinal, signal } = opts;
    const slug = city?.slug || '_unknown';
    const wasWarm = this._warm.has(slug);
    this._warm.add(slug);

    if (this.base && this.live) {
      try { return await this._respondLive(text, city, opts); }
      catch { /* fall through to offline */ }
    }

    const cls = this.classify(text);
    const resp = this._compose(text, city, cls);
    const t0 = performance.now();
    // realistic cache-aware TTFT
    const ttftTarget = wasWarm ? 90 + Math.round(cls.score) : 220 + Math.round(cls.score * 2);
    await sleep(ttftTarget);
    if (signal?.aborted) return resp;
    const ttft_ms = Math.round(performance.now() - t0);
    onLatency?.({ ttft_ms, total_ms: null, cache_hit: wasWarm, source: 'offline' });

    // emit field-by-field with token-level reveal
    onField?.('urgency', resp.urgency);
    onField?.('time_to_act_seconds', resp.time_to_act_seconds);
    await sleep(60);
    onField?.('immediate_actions', []);
    for (let i = 0; i < resp.immediate_actions.length; i++) {
      if (signal?.aborted) break;
      const words = resp.immediate_actions[i].split(' ');
      for (const w of words) { onToken?.('action', { idx: i, chunk: w + ' ' }); await sleep(14); }
      onField?.('action_done', { idx: i, why: resp.reasoning[i] || '' });
      await sleep(40);
    }
    onField?.('who_to_call', resp.who_to_call); await sleep(50);
    onField?.('avoid', resp.avoid); await sleep(40);
    onField?.('jurisdictional_notes', resp.jurisdictional_notes); await sleep(40);
    onField?.('confidence', resp.confidence);

    const total_ms = Math.round(performance.now() - t0);
    resp._meta = { ttft_ms, total_ms, cache_hit: wasWarm, source: 'offline', city_slug: slug };
    onLatency?.(resp._meta);
    onFinal?.(resp);
    return resp;
  }

  // Live path: POST SSE to the FastAPI service and forward parsed events.
  async _respondLive(text, city, opts) {
    const { onField, onLatency, onFinal, signal } = opts;
    const t0 = performance.now();
    const res = await fetch(`${this.base}/emergency`, {
      method: 'POST', signal,
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ situation: text, city: city?.display_name || 'Unknown' }),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', ttft = null, final = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n'); buf = parts.pop();
      for (const p of parts) {
        const line = p.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        const ev = JSON.parse(line.slice(5).trim());
        if (ttft == null) { ttft = Math.round(performance.now() - t0); onLatency?.({ ttft_ms: ttft, total_ms: null, cache_hit: false, source: 'live' }); }
        if (ev.event === 'field') onField?.(ev.field, ev.data);
        else if (ev.event === 'final') final = ev.data;
      }
    }
    const total_ms = Math.round(performance.now() - t0);
    final = final || this._compose(text, city, this.classify(text));
    final._meta = { ttft_ms: ttft, total_ms, cache_hit: false, source: 'live', city_slug: city?.slug };
    onLatency?.(final._meta); onFinal?.(final);
    return final;
  }
}

const DISCLAIMER = 'Decision support only. If life or safety is at risk, call your local emergency number immediately.';
const FALLBACK_CITIES = [{ slug: 'new-york', display_name: 'New York', country: 'USA', flag: '🇺🇸', lat: 40.71, lon: -74.0, primary: '911', numbers: {}, laws: [], notes: [], hospitals: [] }];
const FALLBACK_SCENARIOS = [];
