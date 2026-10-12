/*
 * Doremi DCP-2000 card for Home Assistant.
 * Shipped and registered by the doremi_dcp2000 integration.
 *
 *   type: custom:doremi-dcp2000-card
 *   entity: media_player.dcp_2000
 */
const CARD_VERSION = "0.7.0";

const TEXT = {
  de: {
    playing: "Wiedergabe", paused: "Pause", starting: "Startet …", cued: "Geladen",
    cueing: "Lädt …", stopped: "Gestoppt", ejected: "Leer", off: "Nicht erreichbar",
    remaining: "Rest", nothing: "Kein Inhalt geladen", noSpl: "Keine Show-Playlist geladen",
    prev: "Vorheriger Clip", next: "Nächster Clip", play: "Abspielen", pause: "Pause",
    eject: "Auswerfen", ejectLoad: "Auswerfen / Inhalt laden",
    confirmEject: "Der Server spielt gerade. Wirklich auswerfen? Die Vorstellung wird abgebrochen.",
    load: "Inhalt laden", search: "Suchen …", cancel: "Abbrechen", loading: "Lade Bibliothek …",
    playNow: "Abspielen", pauseFirst: "Pause (erster Frame)", cues: "Automation-Cues",
    noCues: "Keine Automation-Cues", checking: "Prüfe Cues …",
    gotoTc: "Zu Timecode springen", tcHint: "Format HH:MM:SS:FF", ok: "Springen",
    badTc: "Ungültiger Timecode", onlyPause: "Nur in Pause möglich",
    ejectLoaded: "Geladenes auswerfen", events: "Events",
  },
  en: {
    playing: "Playing", paused: "Pause", starting: "Starting …", cued: "Loaded",
    cueing: "Loading …", stopped: "Stopped", ejected: "Empty", off: "Unreachable",
    remaining: "remaining", nothing: "Nothing loaded", noSpl: "No show playlist loaded",
    prev: "Previous clip", next: "Next clip", play: "Play", pause: "Pause",
    eject: "Eject", ejectLoad: "Eject / load content",
    confirmEject: "The server is playing. Really eject? The show will be stopped.",
    load: "Load content", search: "Search …", cancel: "Cancel", loading: "Loading library …",
    playNow: "Play", pauseFirst: "Pause (first frame)", cues: "Automation cues",
    noCues: "No automation cues", checking: "Checking cues …",
    gotoTc: "Go to timecode", tcHint: "Format HH:MM:SS:FF", ok: "Go",
    badTc: "Invalid timecode", onlyPause: "Only while paused",
    ejectLoaded: "Eject loaded content", events: "events",
  },
};

const ICONS = {
  prev: "M6,18V6H8V18H6M9.5,12L18,6V18L9.5,12Z",
  next: "M16,18H18V6H16M6,18L14.5,12L6,6V18Z",
  play: "M8,5.14V19.14L19,12.14L8,5.14Z",
  pause: "M14,19H18V5H14M6,19H10V5H6V19Z",
  eject: "M12,5L5.33,15H18.67M5,17H19V19H5V17Z",
};

const hms = (s) => {
  if (s == null || !isFinite(s)) return "--:--:--";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return [h, m, sec].map((v) => String(v).padStart(2, "0")).join(":");
};
const tc = (seconds, fps) => {
  if (seconds == null || !isFinite(seconds)) return "--:--:--:--";
  const rate = Math.round(fps || 24);
  const frames = Math.max(0, Math.round(seconds * (fps || 24)));
  const f = frames % rate;
  return `${hms(Math.floor(frames / rate))}:${String(f).padStart(2, "0")}`;
};
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const svg = (path) => `<svg viewBox="0 0 24 24"><path d="${path}"/></svg>`;

const STYLE = `
:host { display:block; }
ha-card { padding:16px; overflow:hidden; }
.top { display:grid; grid-template-columns:minmax(0,1fr) minmax(170px,32%); gap:16px; }
.top.nolist { grid-template-columns:minmax(0,1fr); }
@container (max-width: 520px) { .top { grid-template-columns:minmax(0,1fr); } }
.wrap { container-type:inline-size; }
.state { font-weight:600; font-size:0.95em; }
.state.playing { color:var(--success-color,#43a047); }
.state.paused, .state.starting { color:var(--warning-color,#ff9800); }
.state.off { color:var(--error-color,#db4437); }
.state:not(.playing):not(.paused):not(.starting):not(.off) { color:var(--secondary-text-color); }
.titlerow { display:flex; align-items:baseline; gap:12px; margin:6px 0 6px; }
.titlerow .title { flex:1; text-align:center; font-size:1.15em; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.titlerow .side { width:88px; flex:none; }
.spltitle { font-weight:700; }
.bar { position:relative; height:22px; border-radius:6px; overflow:hidden;
  background:var(--secondary-background-color,#eee);
  border:1px solid var(--divider-color,#ccc); touch-action:none; user-select:none; }
.bar .fill { position:absolute; inset:0 auto 0 0; width:0;
  background:var(--dcp-bar-color, var(--primary-color,#03a9f4)); }
.bar .pct { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  font-size:0.8em; font-weight:600; color:var(--primary-text-color);
  text-shadow:0 0 3px var(--card-background-color,#fff); }
.bar .knob { position:absolute; top:0; bottom:0; width:3px; margin-left:-1px;
  background:var(--primary-text-color); display:none; }
.bar.seekable { cursor:ew-resize; }
.bar.seekable .knob { display:block; }
.times { display:flex; justify-content:space-between; font-size:0.85em; margin:4px 0 12px;
  color:var(--secondary-text-color); font-variant-numeric:tabular-nums; }
.times .tc { color:var(--primary-text-color); font-weight:600; border-radius:4px; padding:0 3px; margin-left:-3px; }
.times .tc.clickable { cursor:pointer; text-decoration:underline dotted; }
.times .tc.clickable:hover { background:var(--secondary-background-color); }
.list { border:1px solid var(--divider-color,#ccc); border-radius:8px; overflow-y:auto;
  max-height:230px; font-size:0.85em; background:var(--card-background-color); }
.list .empty { padding:12px; color:var(--secondary-text-color); text-align:center; }
.ev { display:flex; align-items:center; gap:6px; padding:4px 8px; border-bottom:1px solid var(--divider-color,#ddd); }
.ev .dot { width:10px; height:10px; border-radius:50%; flex:none; }
.ev.current .dot { background:var(--success-color,#43a047); box-shadow:0 0 4px var(--success-color,#43a047); }
.ev.current { font-weight:700; background:rgba(var(--rgb-primary-color,3,169,244),0.15); }
.ev .t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; }
.ev .d { color:var(--secondary-text-color); font-variant-numeric:tabular-nums; }
.ev.pattern { background:rgba(127,127,127,0.18); }
.ev.pattern.current { background:rgba(127,127,127,0.3); }
.cue { padding:2px 8px 2px 26px; background:rgba(255,213,79,0.18);
  border-bottom:1px solid var(--divider-color,#ddd); color:var(--primary-text-color); }
.cue::before { content:"⚙ "; opacity:0.6; }
.list.clickable .ev, .list.clickable .cue { cursor:pointer; }
.list.clickable .ev:hover { outline:2px solid var(--primary-color); outline-offset:-2px; }
.buttons { display:flex; justify-content:center; gap:12px; margin-top:14px; padding-top:12px;
  border-top:1px solid var(--divider-color,#ddd); }
button.ctl { width:56px; height:44px; border-radius:10px; border:1px solid var(--divider-color,#ccc);
  background:var(--secondary-background-color,#f3f3f3); cursor:pointer; display:flex;
  align-items:center; justify-content:center; padding:0; }
button.ctl svg { width:28px; height:28px; fill:var(--primary-color,#03a9f4); }
button.ctl:disabled { cursor:default; opacity:0.35; }
button.ctl:not(:disabled):hover { background:var(--divider-color,#e0e0e0); }
button.ctl.busy { opacity:0.6; }
.error { color:var(--error-color,#db4437); font-size:0.85em; margin-top:8px; text-align:center; min-height:1em; }
dialog { border:none; border-radius:16px; padding:0; width:min(640px, 94vw); max-height:94vh;
  box-sizing:border-box; flex-direction:column;
  background:var(--card-background-color,#fff); color:var(--primary-text-color);
  box-shadow:0 10px 40px rgba(0,0,0,.4); }
dialog[open] { display:flex; }
dialog .body { flex:1 1 auto; min-height:0; overflow:auto; }
dialog::backdrop { background:rgba(0,0,0,.45); }
dialog .head { flex:none; padding:16px 20px 0; font-size:1.2em; font-weight:600; }
dialog .body { padding:12px 20px; }
dialog .foot { flex:none; display:flex; gap:8px; justify-content:flex-end; padding:12px 20px 16px; flex-wrap:wrap; }
dialog .foot .grow { flex:1; }
.tabs { display:flex; border-bottom:1px solid var(--divider-color,#ddd); margin-bottom:10px; }
.tabs button { flex:1; background:none; border:none; padding:10px; cursor:pointer; font-size:1em;
  color:var(--secondary-text-color); border-bottom:3px solid transparent; }
.tabs button.active { color:var(--primary-color); border-bottom-color:var(--primary-color); font-weight:600; }
input.search, input.tcin { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:8px;
  border:1px solid var(--divider-color,#ccc); background:var(--secondary-background-color);
  color:var(--primary-text-color); font-size:1em; }
input.tcin { font-variant-numeric:tabular-nums; font-size:1.4em; text-align:center; letter-spacing:1px; }
.lib { height:min(46vh, 380px); min-height:120px; overflow-y:auto; margin-top:8px; border:1px solid var(--divider-color,#ddd); border-radius:8px; }
.lib .item { display:flex; gap:8px; padding:8px 10px; cursor:pointer; border-bottom:1px solid var(--divider-color,#eee); align-items:center; }
.lib .item:hover { background:var(--secondary-background-color); }
.lib .item.sel { background:rgba(var(--rgb-primary-color,3,169,244),0.2); font-weight:600; }
.lib .item .t { flex:1; overflow-wrap:anywhere; }
.lib .item .d { color:var(--secondary-text-color); font-variant-numeric:tabular-nums; white-space:nowrap; font-size:0.9em; }
.lib .msg { padding:16px; color:var(--secondary-text-color); text-align:center; }
.cueinfo { flex:none; min-height:1.4em; font-size:0.85em; padding:0 20px; }
.cueinfo.warn { color:var(--warning-color,#ff9800); font-weight:600; }
dialog button.act { padding:10px 16px; border-radius:20px; border:1px solid var(--divider-color,#ccc);
  background:var(--secondary-background-color); color:var(--primary-text-color); cursor:pointer;
  font-size:0.95em; display:inline-flex; align-items:center; gap:6px; }
dialog button.act svg { width:18px; height:18px; fill:currentColor; }
dialog button.act.primary { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:transparent; }
dialog button.act.danger { background:var(--error-color,#db4437); color:#fff; border-color:transparent; }
dialog button.act:disabled { opacity:0.4; cursor:default; }
.msgtext { line-height:1.4; }
.tcerr { color:var(--error-color); min-height:1.2em; font-size:0.85em; margin-top:6px; }
`;

class DoremiDcp2000Card extends HTMLElement {
  static getStubConfig(hass) {
    const ent = Object.keys(hass.states).find(
      (e) => e.startsWith("media_player.") && hass.states[e].attributes.status_bytes !== undefined,
    );
    return { entity: ent || "media_player.dcp_2000" };
  }

  setConfig(config) {
    if (!config.entity || !config.entity.startsWith("media_player.")) {
      throw new Error("entity (media_player of the DCP-2000) is required");
    }
    this._config = { show_playlist: true, ...config };
    this._built = false;
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").slice(0, 2);
    return TEXT[lang] || TEXT.en;
  }

  get _stateObj() { return this._hass?.states[this._config.entity]; }

  connectedCallback() {
    this._timer = setInterval(() => this._tick(), 250);
  }
  disconnectedCallback() { clearInterval(this._timer); }

  // ---------------------------------------------------------------- build
  _build() {
    const t = this._t;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${STYLE}</style>
      <ha-card><div class="wrap">
        <div class="top ${this._config.show_playlist ? "" : "nolist"}">
          <div class="main">
            <div class="titlerow"><span class="side state" id="state"></span>
              <span class="title" id="clipTitle"></span><span class="side"></span></div>
            <div class="bar" id="clipBar"><div class="fill"></div><div class="knob"></div><div class="pct"></div></div>
            <div class="times"><span><span class="tc" id="clipTc"></span> / <span id="clipDur"></span></span>
              <span>${t.remaining}: <span id="clipRem"></span></span></div>
            <div class="titlerow"><span class="side"></span>
              <span class="title spltitle" id="splTitle"></span><span class="side"></span></div>
            <div class="bar" id="splBar"><div class="fill"></div><div class="pct"></div></div>
            <div class="times"><span><span id="splPos"></span> / <span id="splDur"></span></span>
              <span>${t.remaining}: <span id="splRem"></span></span></div>
          </div>
          ${this._config.show_playlist ? `<div class="list" id="list"></div>` : ""}
        </div>
        <div class="buttons">
          <button class="ctl" id="prev" title="${t.prev}">${svg(ICONS.prev)}</button>
          <button class="ctl" id="playpause"></button>
          <button class="ctl" id="eject" title="${t.ejectLoad}">${svg(ICONS.eject)}</button>
          <button class="ctl" id="next" title="${t.next}">${svg(ICONS.next)}</button>
        </div>
        <div class="error" id="error"></div>
      </div></ha-card>
      <dialog id="dlg"></dialog>`;
    this._built = true;
    this._listSig = null;
    const $ = (id) => root.getElementById(id);
    this.$ = $;
    $("prev").addEventListener("click", () => this._call("media_player", "media_previous_track"));
    $("next").addEventListener("click", () => this._call("media_player", "media_next_track"));
    $("playpause").addEventListener("click", () => this._playPause());
    $("eject").addEventListener("click", () => this._eject());
    $("clipTc").addEventListener("click", () => this._paused && this._openTcDialog());
    this._setupSeek($("clipBar"));
    $("list")?.addEventListener("click", (ev) => {
      const row = ev.target.closest("[data-idx]");
      if (row && this._paused) this._skipTo(Number(row.dataset.idx));
    });
  }

  // --------------------------------------------------------------- state
  _snapshot() {
    const so = this._stateObj;
    if (!so) return null;
    const a = so.attributes;
    const transport = so.state === "off" ? "off" : (a.transport || so.state);
    const playing = transport === "playing";
    const fps = a.edit_rate || a.frame_rate || 24;
    const age = playing && a.media_position_updated_at
      ? Math.max(0, (Date.now() - new Date(a.media_position_updated_at).getTime()) / 1000) : 0;
    const items = a.spl_items || [];
    const idx = a.spl_index;
    const splLoaded = a.mode === "spl";
    let clipDur = a.media_duration;
    if (clipDur != null && clipDur > 360000) clipDur = null; // patterns report "endless"
    let clipPos = a.media_position != null ? a.media_position + age : null;
    if (clipDur != null && clipPos != null) clipPos = Math.min(clipPos, clipDur);
    let splPos = splLoaded && a.spl_position != null ? a.spl_position + age : null;
    const splDur = splLoaded ? a.spl_duration : null;
    if (splDur && splPos != null) splPos = Math.min(splPos, splDur);
    return {
      a, transport, playing, fps, items, idx, splLoaded, clipDur, clipPos, splPos, splDur,
      title: a.media_title || (a.mode === "empty" ? "" : a.clip_id),
      splTitle: splLoaded ? (a.spl_title || "") : "",
    };
  }

  get _paused() { return this._snap?.transport === "paused"; }

  _update() {
    if (!this._built) return;
    const s = (this._snap = this._snapshot());
    const t = this._t;
    const $ = this.$;
    if (!s) {
      $("state").textContent = "";
      $("clipTitle").textContent = `${this._config.entity}?`;
      return;
    }
    $("state").textContent = t[s.transport] || s.transport;
    $("state").className = `side state ${s.transport}`;
    $("clipTitle").textContent = s.title || t.nothing;
    $("clipTitle").title = s.title || "";
    $("splTitle").textContent = s.splTitle || (s.splLoaded ? "" : t.noSpl);
    const pp = $("playpause");
    const showPause = s.playing || s.transport === "starting";
    pp.innerHTML = svg(showPause ? ICONS.pause : ICONS.play);
    pp.title = showPause ? t.pause : t.play;
    pp.disabled = s.transport === "off" || s.a.mode === "empty";
    $("prev").disabled = s.a.mode === "empty" || s.transport === "off";
    $("next").disabled = !s.splLoaded || s.idx == null || s.idx >= s.items.length - 1;
    $("eject").title = s.playing || s.transport === "paused" ? t.eject : t.ejectLoad;
    $("eject").disabled = s.transport === "off";
    $("clipTc").classList.toggle("clickable", this._paused);
    $("clipTc").title = this._paused ? t.gotoTc : "";
    $("clipBar").classList.toggle("seekable", this._paused && s.clipDur > 0);
    const list = $("list");
    if (list) {
      list.classList.toggle("clickable", this._paused);
      list.title = this._paused ? "" : t.onlyPause;
      this._renderList(s);
    }
    this._tick();
  }

  _tick() {
    if (!this._built || !this._hass) return;
    const s = this._snapshot();
    if (!s) return;
    this._snap = s;
    const $ = this.$;
    const clipPos = this._dragPos ?? s.clipPos;
    this._setBar($("clipBar"), clipPos, s.clipDur);
    $("clipTc").textContent = s.a.mode === "empty" ? "--:--:--:--" : tc(clipPos, s.fps);
    $("clipDur").textContent = hms(s.clipDur);
    $("clipRem").textContent = s.clipDur != null && clipPos != null ? hms(s.clipDur - clipPos) : "--:--:--";
    this._setBar($("splBar"), s.splPos, s.splDur);
    $("splPos").textContent = hms(s.splPos);
    $("splDur").textContent = hms(s.splDur);
    $("splRem").textContent = s.splDur != null && s.splPos != null ? hms(s.splDur - s.splPos) : "--:--:--";
  }

  _setBar(bar, pos, dur) {
    const pct = dur > 0 && pos != null ? Math.max(0, Math.min(100, (100 * pos) / dur)) : 0;
    bar.querySelector(".fill").style.width = `${pct}%`;
    const knob = bar.querySelector(".knob");
    if (knob) knob.style.left = `${pct}%`;
    bar.querySelector(".pct").textContent = dur > 0 && pos != null ? `${Math.floor(pct)}%` : "";
  }

  _renderList(s) {
    const list = this.$("list");
    const sig = JSON.stringify([s.a.spl_uuid, s.items.map((i) => i.event_id), s.idx]);
    if (sig === this._listSig) return;
    this._listSig = sig;
    if (!s.splLoaded || !s.items.length) {
      list.innerHTML = `<div class="empty">${esc(this._t.noSpl)}</div>`;
      return;
    }
    list.innerHTML = s.items.map((it, i) => `
      <div class="ev ${esc(it.kind)} ${i === s.idx ? "current" : ""}" data-idx="${i}" title="${esc(it.title)}">
        <span class="dot"></span><span class="t">${esc(it.title)}</span><span class="d">${hms(it.duration)}</span>
      </div>${(it.cues || []).map((c) => `<div class="cue" data-idx="${i}">${esc(c)}</div>`).join("")}`).join("");
    const cur = list.querySelector(".ev.current");
    if (cur) { // scroll inside the list only, never the page
      const top = cur.offsetTop - list.offsetTop;
      if (top < list.scrollTop || top > list.scrollTop + list.clientHeight - cur.offsetHeight) {
        list.scrollTop = Math.max(0, top - cur.offsetHeight);
      }
    }
  }

  // ------------------------------------------------------------- actions
  async _call(domain, service, data = {}, returnResponse = false) {
    this.$("error").textContent = "";
    try {
      const res = await this._hass.callWS({
        type: "call_service", domain, service, service_data: data,
        target: { entity_id: this._config.entity }, return_response: returnResponse,
      });
      return res?.response;
    } catch (err) {
      this.$("error").textContent = err?.message || String(err);
      throw err;
    }
  }

  _playPause() {
    const s = this._snap;
    if (!s) return;
    if (s.playing || s.transport === "starting") this._call("media_player", "media_pause");
    else this._call("media_player", "media_play");
  }

  _eject() {
    const s = this._snap;
    if (!s) return;
    if (s.playing || s.transport === "starting") {
      this._confirm(this._t.confirmEject, this._t.eject, () => this._call("doremi_dcp2000", "eject"));
    } else if (s.transport === "paused") {
      this._call("doremi_dcp2000", "eject");
    } else {
      this._openLoadDialog();
    }
  }

  _skipTo(idx) {
    const it = this._snap?.items[idx];
    if (!it) return;
    this._call("doremi_dcp2000", "skip_to_event", it.event_id ? { event_id: it.event_id } : { index: idx });
  }

  _setupSeek(bar) {
    const posAt = (ev) => {
      const r = bar.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      return frac * (this._snap?.clipDur || 0);
    };
    bar.addEventListener("pointerdown", (ev) => {
      if (!this._paused || !(this._snap?.clipDur > 0)) return;
      bar.setPointerCapture(ev.pointerId);
      this._dragPos = posAt(ev);
      this._tick();
    });
    bar.addEventListener("pointermove", (ev) => {
      if (this._dragPos == null) return;
      this._dragPos = posAt(ev);
      this._tick();
    });
    const end = async (ev, commit) => {
      if (this._dragPos == null) return;
      const pos = posAt(ev);
      if (!commit) { this._dragPos = null; this._tick(); return; }
      const fps = this._snap?.fps || 24;
      try {
        await this._call("doremi_dcp2000", "seek", { timecode: tc(pos, fps) });
      } catch (e) { /* shown in card */ }
      setTimeout(() => { this._dragPos = null; this._tick(); }, 1500);
    };
    bar.addEventListener("pointerup", (ev) => end(ev, true));
    bar.addEventListener("pointercancel", (ev) => end(ev, false));
  }

  // ------------------------------------------------------------- dialogs
  _dialog(html) {
    const dlg = this.$("dlg");
    dlg.innerHTML = html;
    if (!dlg.open) dlg.showModal();
    dlg.onclick = (ev) => { if (ev.target === dlg) dlg.close(); };
    return dlg;
  }

  _confirm(text, action, onOk) {
    const t = this._t;
    const dlg = this._dialog(`
      <div class="head">${esc(action)}</div>
      <div class="body msgtext">${esc(text)}</div>
      <div class="foot"><button class="act" id="no">${t.cancel}</button>
        <button class="act danger" id="yes">${svg(ICONS.eject)} ${esc(action)}</button></div>`);
    dlg.querySelector("#no").onclick = () => dlg.close();
    dlg.querySelector("#yes").onclick = () => { dlg.close(); onOk(); };
    dlg.querySelector("#no").focus();
  }

  _openTcDialog() {
    const t = this._t;
    const s = this._snap;
    const fps = s.fps || 24;
    const dlg = this._dialog(`
      <div class="head">${t.gotoTc}</div>
      <div class="body"><input class="tcin" id="tcin" inputmode="numeric" maxlength="11"
        value="${tc(s.clipPos, fps)}"><div class="tcerr" id="tcerr">${t.tcHint} · ${Math.round(fps)} fps</div></div>
      <div class="foot"><button class="act" id="no">${t.cancel}</button>
        <button class="act primary" id="go">${t.ok}</button></div>`);
    const input = dlg.querySelector("#tcin");
    input.focus();
    input.select();
    input.addEventListener("input", () => { // auto-insert colons while typing digits
      const digits = input.value.replace(/\D/g, "").slice(0, 8);
      input.value = digits.replace(/(\d{2})(?=\d)/g, "$1:");
    });
    const go = async () => {
      const m = /^(\d{1,2}):(\d{2}):(\d{2}):(\d{2})$/.exec(input.value.trim());
      const rate = Math.round(fps);
      if (!m || +m[2] > 59 || +m[3] > 59 || +m[4] >= rate) {
        dlg.querySelector("#tcerr").textContent = `${t.badTc} (${t.tcHint}, FF < ${rate})`;
        return;
      }
      const secs = (+m[1] * 3600 + +m[2] * 60 + +m[3]) + +m[4] / rate;
      if (s.clipDur && secs > s.clipDur) {
        dlg.querySelector("#tcerr").textContent = `${t.badTc} (> ${hms(s.clipDur)})`;
        return;
      }
      dlg.close();
      this._call("doremi_dcp2000", "seek", { timecode: input.value.trim() }).catch(() => {});
    };
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
    dlg.querySelector("#go").onclick = go;
    dlg.querySelector("#no").onclick = () => dlg.close();
  }

  async _openLoadDialog() {
    const t = this._t;
    const s = this._snap;
    this._tab = this._tab || "spl";
    this._sel = null;
    const loaded = s && s.a.mode !== "empty";
    const dlg = this._dialog(`
      <div class="head">${t.load}</div>
      <div class="body">
        <div class="tabs"><button data-tab="cpl">CPL</button><button data-tab="spl">SPL</button></div>
        <input class="search" id="q" placeholder="${t.search}">
        <div class="lib" id="lib"><div class="msg">${t.loading}</div></div>
      </div>
      <div class="cueinfo" id="cueinfo"></div>
      <div class="foot">
        ${loaded ? `<button class="act" id="ej">${svg(ICONS.eject)} ${t.ejectLoaded}</button>` : ""}
        <span class="grow"></span>
        <button class="act" id="no">${t.cancel}</button>
        <button class="act" id="pause" disabled>${svg(ICONS.pause)} ${t.pauseFirst}</button>
        <button class="act primary" id="play" disabled>${svg(ICONS.play)} ${t.playNow}</button>
      </div>`);
    const q = dlg.querySelector("#q");
    const libEl = dlg.querySelector("#lib");
    const tabs = dlg.querySelectorAll(".tabs button");
    const render = () => {
      tabs.forEach((b) => b.classList.toggle("active", b.dataset.tab === this._tab));
      if (!this._lib) return;
      const needle = q.value.trim().toLowerCase();
      const rows = (this._lib[this._tab + "s"] || []).filter(
        (r) => !needle || r.title.toLowerCase().includes(needle) || r.id.toLowerCase().includes(needle),
      );
      libEl.innerHTML = rows.length ? rows.map((r) => `
        <div class="item ${this._sel?.id === r.id ? "sel" : ""}" data-id="${esc(r.id)}">
          <span class="t">${esc(r.title)}</span>
          <span class="d">${r.fps ? `${esc(r.fps)} fps · ` : ""}${r.duration != null ? hms(r.duration) : ""}</span>
        </div>`).join("") : `<div class="msg">–</div>`;
    };
    const select = async (id) => {
      this._sel = { kind: this._tab, id };
      dlg.querySelector("#play").disabled = false;
      dlg.querySelector("#pause").disabled = false;
      render();
      const info = dlg.querySelector("#cueinfo");
      info.className = "cueinfo";
      info.textContent = "";
      if (this._tab !== "spl") return;
      info.textContent = t.checking;
      try {
        const res = await this._call("doremi_dcp2000", "spl_retrieve", { spl_uuid: id }, true);
        if (this._sel?.id !== id) return;
        const xml = new DOMParser().parseFromString(res?.xml || "", "application/xml");
        const cues = [...xml.getElementsByTagNameNS("*", "Action")].map((n) => n.textContent.trim()).filter(Boolean);
        const uniq = [...new Set(cues)];
        info.className = uniq.length ? "cueinfo warn" : "cueinfo";
        info.textContent = uniq.length ? `⚙ ${t.cues}: ${uniq.join(", ")}` : t.noCues;
      } catch (e) {
        info.textContent = "";
      }
    };
    tabs.forEach((b) => b.addEventListener("click", () => {
      this._tab = b.dataset.tab;
      this._sel = null;
      dlg.querySelector("#play").disabled = true;
      dlg.querySelector("#pause").disabled = true;
      dlg.querySelector("#cueinfo").textContent = "";
      render();
    }));
    q.addEventListener("input", render);
    libEl.addEventListener("click", (ev) => {
      const row = ev.target.closest(".item");
      if (row) select(row.dataset.id);
    });
    const load = (start) => {
      if (!this._sel) return;
      const { kind, id } = this._sel;
      dlg.close();
      this._call("doremi_dcp2000", "load_content", { kind, id, start }).catch(() => {});
    };
    dlg.querySelector("#play").onclick = () => load("play");
    dlg.querySelector("#pause").onclick = () => load("pause");
    dlg.querySelector("#no").onclick = () => dlg.close();
    const ej = dlg.querySelector("#ej");
    if (ej) ej.onclick = () => { dlg.close(); this._call("doremi_dcp2000", "eject").catch(() => {}); };
    render();
    try {
      this._lib = await this._call("doremi_dcp2000", "get_library", {}, true);
      render();
    } catch (e) {
      libEl.innerHTML = `<div class="msg">${esc(e?.message || e)}</div>`;
    }
  }
}

if (!customElements.get("doremi-dcp2000-card")) {
  customElements.define("doremi-dcp2000-card", DoremiDcp2000Card);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "doremi-dcp2000-card",
    name: "Doremi DCP-2000",
    description: "Clip and show playlist progress, event list and transport for a Doremi DCP-2000.",
    preview: false,
  });
  console.info(`%c DOREMI-DCP2000-CARD %c ${CARD_VERSION} `,
    "background:#1565c0;color:#fff", "background:#ddd;color:#000");
}
