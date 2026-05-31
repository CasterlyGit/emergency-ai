# emergency-ai — 35-Feature Catalog

> Decision-support only. Always call emergency services (911 or local equivalent) first.
> This catalog documents every capability shipped in v1.0, grouped by tier.

---

## Group A — Headline Features (15)

**A1. Offline-first PWA**
The full app installs to your home screen and works with zero network via a service worker
that caches the app shell, all data files, and scenario corpus at install time. In a real
emergency, cell towers are often congested or destroyed — offline-first is not a nice-to-have,
it is the baseline survival requirement.

**A2. Voice SOS + TTS read-back**
Speak your emergency instead of typing it; the engine transcribes via the Web Speech API,
classifies urgency, and reads the top three immediate actions aloud via TTS. Hands covered in
blood, holding an infant, or in the dark — voice input closes the gap when touch input fails.

**A3. CPR metronome**
A full-screen, audio+visual metronome locked at 100–120 BPM (standard AHA/ERC guideline range,
default 110) counts compressions and alternates with ventilation cues. Correct compression rate
is the single highest-impact variable in out-of-hospital cardiac arrest survival; a locked-tempo
beat prevents the natural drift toward 80 BPM that untrained bystanders exhibit under stress.

**A4. Auto-geo jurisdiction**
On load (or on demand), the app resolves the device's GPS coordinates to the nearest city via
haversine distance over 14+ city centroids, then surfaces that city's emergency numbers,
Good Samaritan laws, and practical local notes automatically. Knowing the local emergency number
and whether you have legal protection for intervening can determine whether a bystander acts.

**A5. Tap-to-dial / tap-to-SMS**
Every emergency number in the response renders as a live `tel:` and `sms:` link — no copy-paste,
no manual dialing. In a high-adrenaline situation, reducing a multi-step action to one tap
measurably cuts time-to-call.

**A6. Adaptive triage questions**
After initial classification, the engine surfaces up to three branching yes/no questions
(e.g., "Is the person breathing?", "Is there a pulse?") that refine urgency and jump to the
most specific scenario branch. Triage logic separates "cardiac arrest" from "heart attack"
before the user types a single medical term — the right protocol is loaded automatically.

**A7. Instant UI translation**
All UI strings and scenario action steps are available in eight languages (English, Spanish,
Hindi, Japanese, French, German, Chinese, Arabic — with full RTL layout for Arabic) without
a server round-trip. Bystanders in multilingual cities can hand the phone to a non-English
speaker and the entire interface re-renders in seconds.

**A8. Precise location share**
One tap copies a Google Maps link containing the device's lat/lon to the clipboard, and a
second tap composes a pre-filled SMS with that link ready to send to your emergency contact or
a 911 dispatcher. Precise coordinates cut first-responder search time in outdoor and high-rise
scenarios.

**A9. Strobe SOS beacon**
Activates the device display as a 5–1–1 (SOS morse) strobe, cycling through maximum brightness
white and full off, with an optional audio tone. Useful for signaling rescuers in low-light
environments (collapsed building, wilderness, flooding) when voice is not viable.

**A10. Contact auto-alert**
A single tap sends a pre-composed SMS to a pre-configured emergency contact containing your
GPS coordinates, the detected urgency level, and a timestamp — no typing required. Mass-casualty
situations often overload 911; alerting a trusted contact creates a parallel rescue thread.

**A11. Siren**
Plays a loud 440 Hz / 880 Hz alternating siren tone through the device speaker to attract
attention or deter threats. Crowd noise and urban environments make verbal distress signals
unreliable at more than a few meters; a 90+ dB tone cuts through.

**A12. Medical-ID card**
A local-storage-only card (never transmitted) stores name, blood type, allergies, medications,
and conditions. It renders full-screen with high contrast on demand, designed to be readable
by a first responder who picks up an unconscious person's phone. The data never leaves the device.

**A13. Guided full-screen mode**
Launches a focused one-step-at-a-time overlay that hides all other UI, advances through
immediate actions with large tap targets, and reads each step aloud. Under extreme stress,
working memory collapses to 1–2 items; guided mode enforces that constraint.

**A14. Scenario quick-grid**
A touch-optimized grid of 20 pre-loaded scenarios (cardiac arrest, choking, stroke, severe
bleeding, anaphylaxis, and more) lets a bystander reach the correct protocol in one tap without
typing anything. Recognition is faster than recall in an emergency; the grid exploits that.

**A15. Honesty layer**
Every response includes a confidence score (0–1), a plain-English disclaimer, and a reminder
to call emergency services. The app never claims to replace professional care. Calibrated
honesty prevents over-reliance that could delay a 911 call.

---

## Group B — Depth Features (10)

**B1. FAST stroke test**
Walks the user step-by-step through the validated FAST mnemonic (Face drooping, Arm weakness,
Speech difficulty, Time to call). Stroke outcome is acutely time-dependent — correct early
recognition by a bystander before professional arrival is the highest-leverage intervention.

**B2. Tourniquet & severe bleeding guide**
Ordered steps for improvised tourniquet application (placement, tightness, time notation) and
wound packing for scenarios where a commercial tourniquet is unavailable. Uncontrolled
hemorrhage is the leading cause of preventable trauma death; correct technique can sustain life
for the minutes until EMS arrives.

**B3. Offline urgency classifier**
A pure-Python (server-side) and pure-JS (client-side) weighted-keyword triage engine assigns
urgency and matches the best scenario without any model API call. This means `/triage` always
responds even when the AI provider is down, rate-limited, or the device is offline — degrading
to a known-good deterministic baseline rather than silence.

**B4. Jurisdiction law explorer**
Surfaces the applicable Good Samaritan statute, bystander CPR immunity laws, and naloxone
standing orders for the resolved city, with citation references. Bystander hesitation due to
fear of legal liability is a documented contributor to delayed intervention — knowing you are
legally protected removes that barrier in real time.

**B5. Incident timeline + PDF export**
Every session records a local-only timestamped event log (scenario triggered, urgency, actions
taken, calls made). A one-tap export renders a structured Markdown/text incident report useful
for handoff to ER staff, insurance, or legal review. The log never leaves the device unless
the user explicitly exports it.

**B6. Poison lookup**
A bundled database of common substances (household chemicals, medications, plants) with per-
substance guidance on whether to induce vomiting, first-aid steps, antidote notes, and the
direct Poison Control number (1-800-222-1222 in the US). Poison Control lines are often busy;
having offline first-aid guidance bridges the gap.

**B7. EpiPen / anaphylaxis protocol**
Step-by-step EpiPen injection guidance (outer mid-thigh, hold 10 seconds, massage, call 911,
position flat with legs elevated, second dose at 5–10 min if available) with a countdown timer.
Anaphylaxis can be fatal within minutes; correct epinephrine technique by a bystander is the
only effective bridge to hospital care.

**B8. Drowning + recovery position**
Covers water rescue safety rules, rescue breathing for non-breathing drowning victims, and the
lateral recovery (HAINES) position for breathing unconscious patients. Both drowning and
unconscious airway management are frequently mishandled by bystanders; clear ordered steps
reduce fatal errors.

**B9. Disaster protocols**
Eight regional and global disaster playbooks (earthquake, house fire, flash flood, active
threat, wildfire, tornado, heatwave, gas leak) with ordered steps and "avoid" lists. Natural
disasters create mass-casualty environments where 911 may be unreachable for hours; a cached,
offline protocol is the only available guidance.

**B10. Explain-why reasoning**
Every immediate action is paired with a one-sentence clinical rationale (e.g., "Tilt head and
lift chin to open the airway"). Understanding why a step matters improves execution fidelity
under stress and helps bystanders adapt when the exact scenario does not match the protocol.

---

## Group C — UI / Experience Features (10)

**C1. Triage-reactive ambient theme**
The entire app background and accent color shift in real time based on detected urgency:
green (low) → yellow (medium) → orange (high) → red (critical), with a breathing radial pulse
that accelerates at higher urgency. The ambient state communicates severity without the user
having to read a label, reducing cognitive load at the worst possible moment.

**C2. Streaming token reveal**
Both live (SSE from the FastAPI service) and offline responses stream field-by-field with
realistic inter-token delays, so content appears progressively rather than all at once. Users
begin reading and acting on the first action within 90–260 ms simulated TTFT, rather than
waiting 1–2 s for a full render — in an emergency, every second of perceived latency matters.

**C3. Live latency HUD gauge**
A monospaced overlay shows real-time TTFT (ms) and total response time, a cache-hit/miss
indicator, and the response source (offline engine vs. live model). Transparency about system
state builds trust: users can immediately see whether they are getting a cached response, a
live AI response, or a pure offline fallback.

**C4. Matte-black neon glass design system**
A unified CSS design token system (`--bg`, `--neon`, `--u-critical`, etc.) renders the app as
a JARVIS-HUD interface with glassmorphism panels, backdrop blur, thin luminous borders, and
JetBrains Mono for all data readouts. High-contrast neon on near-black maximizes legibility
in bright sunlight and low-light environments where emergencies commonly occur.

**C5. Long-press SOS orb**
A large, always-visible orb at the top of the screen activates the full emergency flow on a
0.5 s long press — bypassing text input entirely. Under panic, fine motor control degrades and
multi-step interactions fail; a single large-target gesture keeps the critical path open.

**C6. Haptic + audio feedback**
Every critical action (SOS trigger, metronome beat, step completion, call initiated) fires a
vibration pattern via the Vibration API and a corresponding audio cue. In noisy environments
or when the user cannot watch the screen, haptic feedback confirms that input registered.

**C7. Tilt parallax**
The background layer responds to device orientation via the DeviceOrientation API, creating a
subtle depth effect on the HUD panels. This is not decorative — it reinforces the "this is
a real, installed system" feeling that increases user trust in a high-stress moment where
confidence in the tool affects willingness to act on its guidance.

**C8. Skeleton → content morph**
While the engine classifies and generates a response, anatomically correct skeleton screens
(matching the exact layout of urgency badge, action list, call buttons) fade into real content
rather than showing a blank area or a spinner. Perceived performance is faster than measured
performance; skeleton morphs cut the felt waiting time by anchoring spatial expectations.

**C9. Command palette (⌘K)**
A full-text fuzzy search over all 20 scenarios, 14 cities, 8 languages, and all UI actions,
invoked by ⌘K or a search icon. Power users (paramedics, lifeguards, disaster volunteers) can
jump to any protocol in under two keystrokes without navigating menus.

**C10. Live cache heat-map**
A city grid shows real-time cache state (cold / warm) color-coded per city slug. Cold responses
fetch fresh data; warm responses serve in ~110 ms. The heat-map makes the latency story visible
and demonstrates the retrieval + caching architecture to reviewers in a single glance.

---

## Scenario Coverage (20 bundled)

| ID | Title | Category | Urgency |
|---|---|---|---|
| cardiac-arrest | Cardiac arrest / not breathing | medical | critical |
| choking-adult | Choking — adult | medical | critical |
| choking-infant | Choking — infant | medical | critical |
| severe-bleeding | Severe / uncontrolled bleeding | trauma | critical |
| stroke | Stroke (FAST) | medical | critical |
| anaphylaxis | Anaphylaxis / severe allergic reaction | medical | critical |
| opioid-overdose | Opioid overdose / unresponsive | poison | critical |
| seizure | Seizure | medical | high |
| burns | Burns | trauma | high |
| drowning | Drowning / near-drowning | medical | critical |
| heart-attack | Heart attack (conscious) | medical | critical |
| heat-stroke | Heat stroke / hyperthermia | environmental | critical |
| hypothermia | Hypothermia / severe cold exposure | environmental | high |
| broken-bone | Fracture / broken bone | trauma | medium |
| head-injury | Head injury / concussion | trauma | high |
| poisoning | Poisoning / ingestion | poison | high |
| house-fire | House fire / smoke inhalation | environmental | critical |
| childbirth | Emergency childbirth | medical | critical |
| allergic-reaction | Allergic reaction (non-anaphylactic) | medical | medium |
| electric-shock | Electric shock / electrocution | trauma | critical |

---

*First-aid content follows standard public guidance: AHA/ERC CPR (100–120 BPM), Heimlich
maneuver, FAST stroke protocol, lateral recovery position, EpiPen technique. This app is
decision-support — it does not replace trained responders or professional medical advice.*
