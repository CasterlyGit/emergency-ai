// emergency-ai — purely-visual helpers (no business logic).
// Ambient theme, streaming token reveal, latency gauge, cache heat-map, skeletons, toast.

const $ = id => document.getElementById(id);

// C1 — ambient urgency theme
export function setUrgency(level) {
  document.documentElement.dataset.urgency = level || 'idle';
}

// C2 — streaming token reveal: append a blur-in word to a list item's text node
export function revealToken(li, chunk) {
  const span = document.createElement('span');
  span.className = 'tok';
  span.textContent = chunk;
  (li.querySelector('.li-text') || li).appendChild(span);
}

// C3 — live latency HUD gauge. ttft mapped onto a 0..600ms dial (lower=better, arc fills).
export function showHUD() { $('latency-hud').hidden = false; }
export function updateGauge(ttftMs) {
  const clamped = Math.max(0, Math.min(600, ttftMs));
  const frac = 1 - clamped / 600;                 // faster → fuller arc
  const arc = $('gauge-arc'), needle = $('gauge-needle'), read = $('ttft-read');
  if (arc) arc.style.strokeDashoffset = String(157 * (1 - frac));
  if (needle) needle.style.transform = `rotate(${-90 + frac * 180}deg)`;
  if (read) { read.textContent = String(ttftMs); flash(read, ttftMs < 150 ? 'var(--ok)' : 'var(--accent)'); }
}
export function setHudMeta({ cache_hit, total_ms, source }) {
  if (cache_hit != null) $('cache-read').textContent = cache_hit ? 'HIT' : 'miss';
  if (total_ms != null) $('total-read').textContent = total_ms + 'ms';
  if (source) $('source-read').textContent = source;
}
function flash(el, color) {
  el.animate([{ color }, { color: 'var(--accent)' }], { duration: 700, easing: 'ease-out' });
}

// C10 — cache heat-map: chips per city, warm ones glow
export function renderHeatmap(cities, state) {
  const box = $('cache-heatmap');
  if (!box) return;
  box.innerHTML = '';
  for (const c of cities) {
    const chip = document.createElement('span');
    chip.className = 'heat-chip' + (state[c.slug] === 'warm' ? ' warm' : '');
    chip.textContent = c.flag ? c.flag + ' ' + c.slug.split('-')[0] : c.slug;
    chip.dataset.slug = c.slug;
    box.appendChild(chip);
  }
}
export function warmChip(slug) {
  const chip = document.querySelector(`.heat-chip[data-slug="${slug}"]`);
  if (chip) chip.classList.add('warm');
}

// C8 — skeleton → content morph
export function showSkeleton(container, n = 3) {
  container.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const s = document.createElement('div');
    s.className = 'skel skel-line';
    s.style.opacity = String(1 - i * 0.18);
    container.appendChild(s);
  }
}

let toastTimer;
export function toast(msg, ms = 2600) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add('show'));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => { el.hidden = true; }, 300);
  }, ms);
}

// C7 — device-tilt parallax on the response card (subtle)
export function initParallax(el) {
  if (!window.DeviceOrientationEvent) return;
  let raf;
  window.addEventListener('deviceorientation', e => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = null;
      const x = Math.max(-8, Math.min(8, (e.gamma || 0) / 4));
      const y = Math.max(-6, Math.min(6, (e.beta || 0) / 6 - 6));
      el.style.transform = `perspective(900px) rotateY(${x}deg) rotateX(${-y}deg)`;
    });
  });
}
