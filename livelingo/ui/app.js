// LiveLingo 的介面。所有工作都在背景的程式做，這裡只負責顯示和把按鈕的動作送過去。
(function () {
  "use strict";

  const TOKEN = new URLSearchParams(location.search).get("t") || "";
  const $ = (id) => document.getElementById(id);
  const app = $("app");

  const S = {
    cfg: null, phase: "starting", detail: {}, gpu: {}, mode: {}, fallback: false, version: "",
    sources: {}, messages: [], glossary: [], lessons: [], memory: { mine: 0, fixed: 0, packs: [] }, canWindow: false, view: "main", overlay: { editing: false, running: false }, vu: {},
    record: { on: false, since: 0, count: 0, file: "", name: "", folder: "", saved: 0 },
    hotkeys: { available: false, states: {}, defaults: {} },
    history: { count: 0, bytes: 0 },
    words: {}, wordbook: { count: 0, due: 0 }, recaps: { count: 0, state: "off" },
    ui: { langOpen: false, fixing: null, tab: "audio", wz: 0, devices: null, levels: null, ocr: null, tryId: null }
  };

  // ---------------------------------------------------------------- 小工具

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function t(key, params) {
    const lang = (S.cfg && S.cfg.ui_lang) || "zh-TW";
    let text = (window.I18N[lang] && window.I18N[lang][key]) || window.I18N["en"][key] || key;
    if (params) for (const k in params) text = text.split("{" + k + "}").join(params[k]);
    return text;
  }
  const lk = (code) => (code === "zh-TW" ? "zh" : (code === "zh-CN" ? "zhcn" : code));
  const PAIR_LANGS = ["zh-TW", "zh-CN", "ko", "en", "ja", "vi", "th", "id", "es", "pt", "ru", "de", "fr"];      // 可以選的翻譯語言
  const langName = (code) => t("lang_" + lk(code));
  const isChinese = (code) => code.startsWith("zh");
  // 卡片和輸入框上用的短名字：繁體和簡體都只寫「中文」，免得字太多擠成好幾行；兩邊都是中文時才寫清楚是哪一種
  function shortNames() {
    const mine = S.cfg.my_lang, theirs = S.cfg.their_lang, both = isChinese(mine) && isChinese(theirs);
    const name = (code) => (isChinese(code) ? (both ? langName(code) : t("short_zh")) : t("short_" + code));
    return { mine: name(mine), theirs: name(theirs) };
  }

  async function post(path, body) {
    try {
      const r = await fetch(path, {
        method: "POST", headers: { "Content-Type": "application/json", "X-LiveLingo-Token": TOKEN },
        body: JSON.stringify(body || {})
      });
      return await r.json();
    } catch (e) { return { ok: false, error: String(e) }; }
  }
  async function get(path) {
    try {
      const r = await fetch(path, { headers: { "X-LiveLingo-Token": TOKEN } });
      return await r.json();
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- 設定：改了就存，存好有回饋

  const SAVE_TARGET = {      // 「已儲存」出現在哪個控制項旁邊（沒列的：用 data-key 或 data-change 找）
    theme: '[data-act="theme"][aria-pressed="true"]', accent: '[data-act="accent"][aria-pressed="true"]',
    ui_lang: '[data-act="ui-lang"][aria-pressed="true"]', tone: '[data-act="tone"][aria-pressed="true"]', tone_keep: '[data-act="tone-keep"][aria-pressed="true"]',
    quality: '[data-act="quality"][aria-pressed="true"]', my_lang: "#sel-my", their_lang: "#sel-their", their_auto: "#sel-their",
    speaker_device: '[data-act="device"][data-key="speaker"]', mic_device: '[data-act="device"][data-key="mic"]',
    transcript_dir: '[data-act="rec-choose"]', region: '[data-act="region"]', overlay_rect: '[data-act="overlay-edit"]', overlay_map: "#screen-map",
    overlay_look: '[data-act="overlay-look-reset"]', hotkeys: '[data-act="hotkey-reset"]'
  };
  let savedTimer = null;
  function placeSaved() {      // 設定頁重畫之後也會再叫一次，回饋不會因為重畫就不見
    const chip = $("saved-chip"), mark = S.ui.saved;
    document.querySelectorAll(".just-saved").forEach((el) => el.classList.remove("just-saved"));
    if (!mark || Date.now() > mark.until) { chip.hidden = true; return; }
    const page = $("settings-page");
    const el = S.view === "settings" && (page.querySelector(mark.key[0] === "#" ? mark.key : mark.key.startsWith("preset-") ? `[data-act="overlay-preset"][data-code="${mark.key.slice(7)}"]` : SAVE_TARGET[mark.key] || `[data-key="${mark.key}"][aria-pressed="true"]`)
      || page.querySelector(`[data-key="${mark.key}"], [data-change="${mark.key}"]`));
    const row = el && (el.closest(".setting-row, .range-row, .field") || el.closest(".btn-row, .pills, .swatches, .packs, .device.app, .region-card, .presets, .screen-map, .layout-item"));
    if (row) row.classList.add("just-saved");
    chip.hidden = false;
    if (el) {
      const r = el.getBoundingClientRect(), w = chip.offsetWidth, h = chip.offsetHeight;
      chip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + "px";
      chip.style.top = (r.top - h - 6 > 8 ? r.top - h - 6 : r.bottom + 6) + "px";
      chip.style.right = "auto";
    } else { chip.style.left = "auto"; chip.style.right = "20px"; chip.style.top = "64px"; }
  }
  function flashSaved(key) {
    const chip = $("saved-chip");
    S.ui.saved = { key, until: Date.now() + 1700 };
    chip.innerHTML = `${ICON.check}<span>${esc(t("saved"))}</span>`;
    chip.hidden = false;
    chip.classList.remove("show"); void chip.offsetWidth; chip.classList.add("show");      // 連續改幾項時動畫重新開始
    placeSaved();
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { S.ui.saved = null; placeSaved(); }, 1750);
  }
  async function saveCfg(patch, key) {      // 使用者改設定都走這裡：存好顯示「已儲存」，存不進去說清楚
    const r = await post("/api/config", patch);
    if (r && r.ok && r.saved) flashSaved(key || Object.keys(patch)[0]);
    else toast(t("n_save_failed", { error: (r && r.error) || "" }), 8000, "error");
    return r;
  }

  let toastTimer = null;
  function toast(text, ms, level) {      // level：info（預設）/ warn / error
    const el = $("toast");
    el.textContent = text;
    el.dataset.toast = level || "info";      // 不能叫 data-level：那是音量條用的，會被 paintLevels 當成音量條改寬度
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms || 4200);
  }

  const ICON = {
    screen: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
    listen: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/></svg>',
    mic: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    quality: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 6h9M8 4v2M6 6c0 4 3 7 6 8M12 6c0 3-3 7-7 8"/><path d="M13 20l4-9 4 9M14.5 17h5"/></svg>',
    globe: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg>',
    check: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" style="stroke-width:3"><path d="M5 12l5 5 9-10"/></svg>',
    back: '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24"><path d="M20 5v7a4 4 0 0 1-4 4H5m4-4l-4 4 4 4"/></svg>',
    info: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    help: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2 1-1.2 1.8M12 16.6h.01"/></svg>',
    back: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
    x: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    up: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 16V8M8.5 11.5L12 8l3.5 3.5"/></svg>',
    book: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5M9 8h7M9 12h5"/></svg>',
    clock: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    frame: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" style="stroke-width:2.2"><path d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"/></svg>',
    chat: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 7h3M10 7h10M4 12h3M10 12h7M4 17h3M10 17h9"/></svg>',
    swap: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path d="M7 7h13l-3-3M17 17H4l3 3"/></svg>',
    bubble: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-8l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/></svg>',
    disk: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>',
    shield: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  };
  const MARK = window.BRAND.mark;      // 品牌標誌和名字都在 brand.js
  const WORDMARK = `${window.BRAND.name[0]}<b>${window.BRAND.name[1]}</b>`;

  // ---------------------------------------------------------------- 固定文字

  function applyI18n() {
    const htmlLang = { "zh-TW": "zh-Hant", "zh-CN": "zh-Hans", en: "en", ko: "ko", ja: "ja", vi: "vi", th: "th", id: "id", es: "es", pt: "pt", ru: "ru", de: "de", fr: "fr" }[S.cfg.ui_lang] || "en";
    document.documentElement.lang = htmlLang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-label]").forEach((el) => {
      el.setAttribute("aria-label", t(el.dataset.i18nLabel));
      el.title = t(el.dataset.i18nLabel);
    });
  }

  // ---------------------------------------------------------------- 主題

  const ACCENTS = { mint: ["#4ADE9A", "#076F49"], blue: ["#6CB6FF", "#175DB3"], violet: ["#B7A1FF", "#6044CF"], rose: ["#FF8FA8", "#B0264B"], cyan: ["#4DD8E6", "#096A75"] };
  const systemDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const isDark = () => (S.cfg.theme === "system" ? !systemDark || systemDark.matches : S.cfg.theme !== "light");

  function applyTheme() {
    const root = document.documentElement, dark = isDark();
    root.dataset.theme = dark ? "dark" : "light";
    if (S.cfg.accent && S.cfg.accent !== "mint") root.dataset.accent = S.cfg.accent; else delete root.dataset.accent;
    // Windows 的標題列也換成同一個顏色
    const css = getComputedStyle(root), hex = (name) => css.getPropertyValue(name).trim();
    const chrome = { caption: hex("--surface"), text: hex("--text"), dark };
    if (JSON.stringify(chrome) !== S.ui.chrome && /^#[0-9a-f]{6}$/i.test(chrome.caption)) { S.ui.chrome = JSON.stringify(chrome); post("/api/chrome", chrome); }
  }
  if (systemDark && systemDark.addEventListener) systemDark.addEventListener("change", () => { if (S.cfg && S.cfg.theme === "system") applyTheme(); });

  function themeHTML() {
    const pill = (id) => `<button class="pill tall" data-act="theme" data-code="${id}" aria-pressed="${(S.cfg.theme || "dark") === id}">${esc(t("theme_" + id))}</button>`;
    const dark = isDark();
    const swatch = (id, n) => `<button class="swatch" data-act="accent" data-code="${id}" style="--sw:${ACCENTS[id][dark ? 0 : 1]}" aria-pressed="${(S.cfg.accent || "mint") === id}" aria-label="${esc(t("accent_title"))} ${n + 1}" title="${esc(t("accent_title"))} ${n + 1}"></button>`;
    return `<div class="pills">${["dark", "light", "system"].map(pill).join("")}</div>
      <div class="sub">${esc(t("accent_title"))}</div>
      <div class="swatches">${Object.keys(ACCENTS).map(swatch).join("")}</div>`;
  }

  // ---------------------------------------------------------------- 主畫面

  function percent() {
    const d = S.detail || {};
    return d.total_mb ? Math.min(100, Math.round((d.done_mb || 0) * 100 / d.total_mb)) : 0;
  }

  function renderStatus() {
    const el = $("status");
    el.dataset.phase = S.phase;
    $("status-text").textContent = t("st_" + S.phase, { percent: percent() });
    el.title = $("status-text").textContent;      // 視窗很窄的時候文字會收起來，只剩圓點：滑鼠停上去還看得到
    renderQualityChip();
  }

  // 主畫面上標出現在用哪一種翻譯品質；滑鼠停上去說明原因，按下去到「翻譯品質」頁
  function qualityReason() {
    const tier = S.gpu.tier || "cpu";
    if (S.mode.quality === "high") return t("chip_why_high", { name: S.gpu.name || "" });
    if (S.fallback) return t("gpu_fallback");
    if (tier === "high") return t("chip_why_chosen");
    if (S.gpu.name) return t("gpu_small", { name: S.gpu.name, gb: gb(S.gpu.vram_mb) });
    return t("gpu_none");
  }

  function renderQualityChip() {
    const chip = $("quality-chip");
    const known = S.phase === "ready" && S.mode && S.mode.quality;
    chip.hidden = !known;
    if (!known) return;
    const high = S.mode.quality === "high";
    chip.dataset.q = high ? "high" : (S.fallback ? "fallback" : "standard");
    chip.textContent = t(high ? "chip_high" : "chip_standard");
    chip.title = qualityReason();
    chip.setAttribute("aria-label", chip.textContent + " " + chip.title);
  }

  function sourceHint(key) {
    const s = S.sources[key] || {};
    const names = shortNames();
    if (s.lost) return t("src_lost");
    if (s.busy) return t("src_busy");
    if (s.error && !s.on) return t("src_error");
    if (key === "screen") return s.on ? t("screen_on") : t("screen_off");
    const device = s.device || (key === "listen" ? S.cfg.speaker_name : S.cfg.mic_name) || t("default_device");
    if (key === "listen" && s.on && s.apps && !s.speaking) {      // 只聽指定的程式：說現在聽到哪幾個，都還沒開就說在等
      return s.device ? t("listen_apps_on", { apps: s.device }) : t("listen_apps_wait", { apps: (S.cfg.listen_apps || []).map(appName).join("、") });
    }
    if (key === "listen") return s.on ? (s.speaking ? t("listen_speaking") : t("listen_on", { device })) : t("listen_off");
    if (s.on && S.cfg.ptt && !s.speaking) return t("mic_ptt", { key: keyName(S.cfg.ptt_key) });
    return s.on ? (s.speaking ? t("mic_speaking") : t("mic_on", { device })) : t("mic_off", names);
  }

  function renderSources() {
    const html = ["screen", "listen", "mic"].map((key) => {
      const s = S.sources[key] || {};
      const state = s.busy ? t("busy") : (s.on ? t("on") : t("off"));
      return `<button class="card" data-act="source" data-key="${key}" aria-pressed="${s.on ? "true" : "false"}"
        data-busy="${s.busy ? "true" : "false"}" data-error="${s.error && !s.on ? "true" : "false"}"
        data-speaking="${s.on && s.speaking ? "true" : "false"}">
        ${ICON[key]}<div class="name">${esc(t("src_" + key))}</div><span class="state">${esc(state)}</span>
        <div class="hint">${esc(sourceHint(key))}</div>
        ${key !== "screen" && s.on ? `<div class="vu" aria-hidden="true"><i data-vu="${key}"></i></div>` : ""}
      </button>`;
    }).join("");
    $("source-cards").innerHTML = html;
    paintVu();
    renderMicRow();
    const row = $("region-row");
    const live = regions().filter((x) => x.on);
    const r = live[0];
    const showRegion = !!(r && (S.sources.screen || {}).on);
    row.hidden = !(showRegion || S.messages.length);
    // 只讀一個區域：可以直接重框；讀好幾個：到設定頁管理
    row.innerHTML = (showRegion
      ? (live.length > 1
        ? `<span>${esc(t("regions_now", { n: live.length }))} · <button data-act="nav" data-to="screen">${esc(t("nav_screen"))}</button></span>`
        : `<span>${esc(t("region_now", { w: r.width, h: r.height }))} · <button data-act="region" data-id="${esc(r.id)}">${esc(t("region_change"))}</button></span>`)
      : "<span></span>")
      + (S.messages.length ? `<button data-act="clear">${esc(t("clear"))}</button>` : "");
  }

  function paintVu() {
    document.querySelectorAll("[data-vu]").forEach((el) => {
      el.style.width = Math.round((S.vu[el.dataset.vu] || 0) * 100) + "%";
    });
  }

  // 麥克風有狀況時（完全沒聲音、被 Windows 擋住、打不開、正在自動找）在卡片下面說清楚，並給按鈕
  function micNotice() {
    const mic = S.sources.mic || {};
    const find = S.ui.find;
    const findBtn = `<button class="primary" data-act="find-mic">${esc(t("find_btn"))}</button>`;
    const winBtn = `<button class="ghost" data-act="mic-settings">${esc(t("win_mic_settings"))}</button>`;
    if (find && find.state === "listening") {
      return { text: t("find_listening"), busy: true,
               buttons: `<button class="ghost" data-act="find-cancel">${esc(t("cancel"))}</button>` };
    }
    if (find && find.state === "found") {
      return { text: t("find_found", { name: find.name }), busy: true,
               buttons: `<button class="primary" data-act="find-dismiss">${esc(t("dismiss"))}</button>
                 <button class="ghost" data-act="find-undo">${esc(t("find_undo", { name: find.previous.name || t("default_device") }))}</button>` };
    }
    if (find && find.state === "none") {
      return { text: find.blocked ? t("find_none_blocked") : t("find_none"),
               buttons: `<button class="primary" data-act="find-mic">${esc(t("find_again"))}</button>${winBtn}
                 <button class="ghost" data-act="find-dismiss">${esc(t("dismiss"))}</button>` };
    }
    if (mic.on && mic.quiet === "blocked") return { text: t("mic_blocked"), buttons: winBtn + findBtn };
    if (mic.on && mic.quiet) return { text: t("mic_silent"), buttons: findBtn };
    if (mic.error && !mic.on && !mic.busy) return { text: t("mic_failed"), detail: mic.error, buttons: findBtn, level: "error" };
    return null;
  }

  function noticeHTML(n, icon) {
    return `<div class="mic-note ${n.busy ? "info" : (n.level || "warn")}">${icon}<div><div class="text">${esc(n.text)}</div>
      ${n.detail ? `<div class="detail">${esc(n.detail)}</div>` : ""}
      ${n.buttons ? `<div class="btns">${n.buttons}</div>` : ""}</div></div>`;
  }

  function micNoticeHTML() {
    const n = micNotice();
    return n ? noticeHTML(n, ICON.mic) : "";
  }

  function renderMicRow() {
    const row = $("mic-row");
    const listen = S.sources.listen || {};
    const html = micNoticeHTML()
      + (listen.error && !listen.on && !listen.busy ? noticeHTML({ text: t("listen_failed"), detail: listen.error, level: "error" }, ICON.listen) : "");
    row.hidden = !html;
    row.innerHTML = html;
  }

  // 字幕記錄：右上角的按鈕（開始 / 結束並儲存），存好之後在來源卡片下面說存到哪了
  function recClock() {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - S.record.since));
    const two = (n) => String(n).padStart(2, "0");
    return (s >= 3600 ? Math.floor(s / 3600) + ":" : "") + two(Math.floor(s / 60) % 60) + ":" + two(s % 60);
  }

  function renderRecord() {
    const r = S.record, btn = $("btn-record");
    btn.setAttribute("aria-pressed", r.on ? "true" : "false");
    btn.title = t(r.on ? "rec_stop" : "rec_start");
    btn.disabled = !!r.saving;
    btn.innerHTML = r.saving ? `<i class="rec-dot live"></i><span>${esc(t("rec_saving"))}</span>` : r.on
      ? `<i class="rec-dot live"></i><span class="label">${esc(t("rec_stop"))}</span><span class="rec-time" id="rec-time">${recClock()}</span>`
      : `<i class="rec-dot"></i><span class="label">${esc(t("rec_start"))}</span>`;
    const row = $("rec-row");
    const show = !r.on && r.file && !S.ui.recHidden;
    row.hidden = !show;
    row.innerHTML = show ? noticeHTML({
      level: "info", text: t("rec_saved", { name: r.name, count: r.saved }), detail: r.folder,
      buttons: `<button class="primary" data-act="rec-open" data-what="file">${esc(t("rec_open_file"))}</button>
        <button class="ghost" data-act="rec-open" data-what="folder">${esc(t("rec_open_folder"))}</button>
        <button class="ghost" data-act="rec-dismiss">${esc(t("dismiss"))}</button>` }, ICON.check) : "";
  }
  setInterval(() => { const el = S.record.on && $("rec-time"); if (el) el.textContent = recClock(); }, 1000);

  function recordHTML() {
    return `<div class="setting-row"><div><div>${esc(t("rec_folder"))}</div><div class="desc path">${esc(S.record.folder)}</div></div></div>
      <div class="btn-row">${S.canWindow ? `<button class="ghost" data-act="rec-choose">${esc(t("rec_choose"))}</button>` : ""}
        <button class="ghost" data-act="rec-open" data-what="folder">${esc(t("rec_open_folder"))}</button></div>
      ${switchRow("transcript_original", t("rec_original"), t("rec_original_desc"))}`;
  }

  function renderLangs() {
    const auto = !!S.cfg.their_auto;
    $("pair-label").textContent = (auto ? t("auto_pair", { lang: langName(S.cfg.their_lang) }) : langName(S.cfg.their_lang)) + " ⇄ " + langName(S.cfg.my_lang);
    $("lang-chip").setAttribute("aria-expanded", S.ui.langOpen ? "true" : "false");
    const picker = $("lang-picker");
    picker.hidden = !S.ui.langOpen;
    if (S.ui.langOpen) {
      const opts = (side, current) => PAIR_LANGS.map((code) =>
        `<button class="pill" data-act="set-lang" data-side="${side}" data-code="${code}" aria-pressed="${current === code}">${esc(langName(code))}</button>`).join("");
      picker.innerHTML = `
        <div class="group"><div class="dim small">${esc(t("their_lang"))}</div><div class="opts">
          <button class="pill" data-act="set-auto" aria-pressed="${auto}">${esc(t("auto_detect"))}</button>${opts("their_lang", auto ? "" : S.cfg.their_lang)}</div>
          ${auto ? `<div class="dim small auto-now">${esc(t("auto_hint", { lang: langName(S.cfg.their_lang) }))}</div>` : ""}</div>
        <div class="group"><div class="dim small">${esc(t("my_lang"))}</div><div class="opts">${opts("my_lang", S.cfg.my_lang)}</div></div>
        <div class="foot"><button class="primary" data-act="toggle-langs">${esc(t("done"))}</button></div>`;
    }
    const names = shortNames();
    $("say-label").textContent = t("say_label", names);
    $("say").placeholder = t("say_placeholder");
  }

  function bubbleHTML(m) {
    // 對方還在說、先顯示出來的那一則（即時字幕）：原文後面有「…」，說完會換成整句
    let body = `<div class="orig${m.live ? " hearing" : ""}">${esc(m.original)}</div>`;
    if (m.mention && !m.pending && !m.error) body = `<div class="mention-tag">${esc(t("mention_tag", { w: m.mention }))}</div>` + body;      // 對方提到我設的名字或關鍵字
    // 辨識的時候沒什麼把握：標出來，按下去可以把原文改對再重翻
    const unsure = m.unsure && !m.pending
      ? `<button class="unsure" data-act="fix" data-id="${m.id}">${ICON.info}<span>${esc(t(m.via === "screen" ? "unsure_seen" : "unsure_heard"))}</span></button>` : "";
    if (m.error) {
      // 先用看得懂的話說原因；電腦回報的原文放在提示裡（滑鼠移上去看得到），不知道原因的才直接顯示
      body += `<div class="text failed" title="${esc(m.error)}">${esc(m.why && m.why !== "other" ? t("err_" + m.why) : t("failed", { error: m.error }))}</div>` +
        `<div class="actions"><button data-act="again" data-id="${m.id}">${esc(t("retry"))}</button></div>`;
    } else if (m.pending) {
      // 翻到一半的譯文先顯示（一個字一個字出來）；還沒有字的時候才寫「翻譯中」
      body += m.partial ? `<div class="text streaming" id="part-${m.id}">${esc(m.partial)}</div>` : `<div class="text pending" id="part-${m.id}">${esc(t("translating"))}</div>`;
    } else {
      if (m.translated === m.original) body = "";      // 兩邊同一種語言時原文和譯文一樣，顯示一次就好
      body += unsure + `<div class="text">${esc(m.translated)}</div>`;
      if (m.lost && m.lost.length) body += `<div class="lost">${ICON.info}<span>${esc(t("numbers_lost", { numbers: m.lost.join(" · ") }))}</span></div>`;
      if (m.back && S.cfg.back_translate) body += `<div class="back">${ICON.back}<span>${esc(t("back", { text: m.back }))}</span></div>`;
      if (m.pron && S.cfg.show_pronunciation) body += `<div class="pron">${esc(t("pron", { text: m.pron }))}</div>`;
      if (m.copied) body += `<div class="copied">${ICON.check}<span>${esc(t("copied_hint"))}</span></div>`;
      body += `<div class="actions"><button data-act="fix" data-id="${m.id}">${esc(t("wrong"))}</button>` +
        `<button data-act="copy" data-id="${m.id}">${esc(t("copy"))}</button>` +
        (m.who === "them" ? `<button data-act="replies" data-id="${m.id}">${esc(t("reply_ideas"))}</button>` : "") + `</div>`;
      body += repliesHTML(m.id) + wordsHTML(m);
    }
    return body;
  }

  // 語言學習：對方的句子下面標一兩個生字和意思，按「收進單字本」或「我會了」（以後不再標）
  const wordKey = (w) => w.lang + "|" + w.w.toLowerCase();
  function wordsHTML(m) {
    const items = S.words[m.id];
    if (!S.cfg.learn_mode || m.who === "me" || !items || !items.length) return "";
    const saved = S.ui.wordSaved || {};
    return `<div class="words"><div class="words-head">${esc(t("learn_new"))}</div>${items.map((w, i) => `<div class="word">
      <span class="word-w">${esc(w.w)}</span><span class="word-m">${esc(w.meaning)}</span>
      <button class="ghost" data-act="word-add" data-id="${m.id}" data-n="${i}" ${saved[wordKey(w)] ? "disabled" : ""}>${esc(t(saved[wordKey(w)] ? "learn_saved" : "learn_save"))}</button>
      <button class="ghost" data-act="word-know" data-id="${m.id}" data-n="${i}" title="${esc(t("learn_know_desc"))}">${esc(t("learn_know"))}</button></div>`).join("")}</div>`;
  }

  // 回話建議：對方那句下面列 3 句（對方的語言 + 我的語言的意思），按一下複製
  function repliesHTML(id) {
    const r = S.replies || {};
    if (r.id !== id || r.state === "off" || r.closed) return "";
    if (r.state === "thinking") return `<div class="replies" id="replies-${id}"><div class="pending">${esc(t("reply_thinking"))}</div></div>`;
    if (!r.items || !r.items.length) return `<div class="replies" id="replies-${id}"><div class="pending">${esc(t("reply_none"))}</div></div>`;
    return `<div class="replies" id="replies-${id}"><div class="replies-head">${esc(t("reply_head"))}<button class="icon-btn" data-act="replies-close" aria-label="${esc(t("close"))}">${ICON.x}</button></div>
      ${r.items.map((it, i) => `<button class="reply" data-act="reply-copy" data-n="${i}"><span class="reply-text">${esc(it.text)}</span><span class="reply-meaning">${esc(it.meaning)}</span>
        <span class="reply-copy">${esc(S.ui.replyCopied === i ? t("copied") : t("copy"))}</span></button>`).join("")}</div>`;
  }

  function metaHTML(m) {
    const icon = m.via === "screen" ? ICON.screen : (m.via === "heard" ? ICON.listen : (m.via === "spoken" ? ICON.mic : ""));
    // 語音的句子：後面標從說完到翻好花了幾秒
    const took = m.took > 0 && !m.pending ? ` · <span class="took" title="${esc(t("took_hint"))}">${esc(t("took", { s: Number(m.took).toFixed(1) }))}</span>` : "";
    const place = m.place ? ` · ${esc(m.place)}` : "";      // 同時讀好幾個聊天區時，標是哪一個讀到的
    // 用的是記住的翻法（不是剛剛翻的）：標「翻過的」或「朋友的」
    const kept = m.kept && !m.pending && !m.error && !m.taught ? ` · <span class="kept" title="${esc(t("kept_hint"))}">${esc(t("kept_" + m.kept))}</span>` : "";
    // 有分出是誰說的：「對方」換成那個人（顏色的點加名字），點下去可以去改名字
    const sp = S.speakers || {};
    const tagged = m.speaker && S.cfg.tell_speakers && m.speaker_run === sp.run;
    const who = tagged ? `<button class="spk" data-act="spk-open" title="${esc(t("spk_open"))}"><span class="spk-dot spk-c${(m.speaker - 1) % 6}"></span>${esc(speakerLabel(m.speaker))}</button>` : esc(t("who_" + m.who));
    return `${icon}<span>${who} · ${esc(t("via_" + m.via))}${place}${took}${kept}</span>`;
  }

  // ---- 分辨說話者
  function speakerLabel(n) {
    const p = ((S.speakers || {}).people || []).find((x) => x.id === n);
    return (p && p.name) || t("spk_n", { n });
  }
  function speakerLangsHTML(p) {      // 這個人說的語言：主語言（沒選 = 照「對方的語言」）、副語言（要先有主語言）
    const pick = (which, current, none, off) => `<label class="spk-lang"><span>${esc(t(which === "lang" ? "spk_lang" : "spk_lang2"))}</span>
      <select id="spk-${which}-${p.id}" data-spk-lang="${p.id}" data-which="${which}" ${off ? "disabled" : ""}><option value="">${esc(t(none))}</option>${PAIR_LANGS.map((c) =>
        `<option value="${c}" ${c === current ? "selected" : ""}>${esc(langName(c))}</option>`).join("")}</select></label>`;
    return `<div class="spk-langs">${pick("lang", p.lang || "", "spk_lang_auto", false)}${pick("lang2", p.lang2 || "", "spk_lang2_none", !p.lang)}</div>`;
  }
  function speakersHTML() {
    const sp = S.speakers || {};
    let more = "";
    if (S.cfg.tell_speakers) {
      if (sp.state === "downloading") more = `<div class="note">${ICON.info}<span id="spk-percent">${esc(t("spk_downloading", { mb: sp.mb, percent: sp.percent || 0 }))}</span></div>`;
      else if (sp.state === "loading") more = `<div class="note">${ICON.info}<span>${esc(t("spk_loading"))}</span></div>`;
      else if (sp.state === "failed") more = `<div class="note warn">${ICON.info}<span>${esc(t("spk_failed", { error: sp.error || "" }))}</span>
        <button class="primary small" data-act="spk-retry">${esc(t("retry"))}</button></div>`;
      else if (sp.state === "ready" && !(sp.people || []).length) more = `<div class="note">${ICON.info}<span>${esc(t("spk_none"))}</span></div>`;
      else if (sp.state === "ready") more = `<div class="sub">${esc(t(sp.remember ? "spk_list_kept" : "spk_list"))}</div>` + sp.people.map((p) => `
        <div class="setting-row spk-row"><label for="spk-name-${p.id}"><span class="spk-dot spk-c${(p.id - 1) % 6}"></span><span>${esc(t("spk_n", { n: p.id }))}</span><span class="desc">${esc(p.known && !p.count ? t("spk_known") : t("spk_count", { n: p.count }) + (p.known ? " · " + t("spk_kept") : ""))}</span></label>
          <input type="text" id="spk-name-${p.id}" data-speaker="${p.id}" value="${esc(p.name || "")}" maxlength="24" placeholder="${esc(t("spk_name"))}">${speakerLangsHTML(p)}</div>`).join("") +
        `<div class="sub">${esc(t("spk_lang_hint"))}</div><div class="btn-row"><button class="ghost small" data-act="spk-forget">${esc(t("spk_forget"))}</button></div>`;
      more = switchRow("remember_speakers", t("remember_speakers"), t("remember_speakers_desc")) + more;      // 要不要把取過名字的人記到下次
    }
    return `<section><div><h2>${esc(t("spk_title"))}</h2><div class="sub">${esc(t("spk_hint"))}</div></div>
        ${switchRow("tell_speakers", t("tell_speakers"), t("tell_speakers_desc"))}${more}</section>`;
  }

  function fixHTML(m) {
    const words = [];
    m.original.split(/[\s,.!?~…·、，。！？「」『』()（）\[\]<>"']+/).forEach((w) => {
      if (w && words.indexOf(w) < 0 && words.length < 16) words.push(w);
    });
    // 語音和螢幕上讀來的句子：可能是原文就辨識錯了，先讓他改原文
    const edit = m.via === "typed" ? "" : `
      <label class="dim small" for="edit-${m.id}">${esc(t(m.via === "screen" ? "edit_label_seen" : "edit_label_heard"))}</label>
      <textarea id="edit-${m.id}" class="edit-src" rows="2" maxlength="2000">${esc(m.original)}</textarea>
      <button class="primary" data-act="edit-save" data-id="${m.id}">${esc(t("edit_save"))}</button>`;
    return `<div class="fix" data-id="${m.id}">${edit}
      <label class="dim small ${edit ? "teach-label" : ""}" for="fix-src-${m.id}">${esc(t("fix_label"))}</label>
      <div class="words">${words.map((w) => `<button type="button" data-act="fix-word" data-word="${esc(w)}">${esc(w)}</button>`).join("")}</div>
      <div class="pair">
        <input id="fix-src-${m.id}" class="fix-src" aria-label="${esc(t("fix_word"))}" placeholder="${esc(t("fix_word"))}" maxlength="80">
        <span class="dim small">${esc(t("fix_to"))}</span>
        <input class="fix-dst" aria-label="${esc(t("fix_right"))}" placeholder="${esc(t("fix_right"))}" maxlength="80">
      </div>
      <button class="primary" data-act="fix-save" data-id="${m.id}">${esc(t("fix_save"))}</button>
      <label class="dim small teach-label" for="teach-${m.id}">${esc(t("teach_label"))}</label>
      <textarea id="teach-${m.id}" class="teach-dst" rows="2" maxlength="500">${esc(m.translated || "")}</textarea>
      <button class="primary" data-act="teach-save" data-id="${m.id}">${esc(t("teach_save"))}</button>
    </div>`;
  }

  function nearBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 90; }

  function upsertMessage(m) {
    const box = $("messages");
    const stick = nearBottom(box);
    const empty = box.querySelector(".empty");
    if (empty) empty.remove();
    let el = $("m-" + m.id);
    if (!el) {
      el = document.createElement("div");
      el.id = "m-" + m.id;
      el.innerHTML = '<div class="meta"></div><div class="bubble"><div class="bm"></div><div class="fixslot"></div></div>';
      box.appendChild(el);
    }
    el.className = "msg " + (m.who === "me" ? "me" : "them");
    el.querySelector(".meta").innerHTML = metaHTML(m);
    el.querySelector(".bm").innerHTML = bubbleHTML(m);
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function renderMessages() {
    const box = $("messages");
    box.innerHTML = "";
    if (!S.messages.length) {
      const step = (n, text) => `<li><i>${n}</i><span>${esc(text)}</span></li>`;
      box.innerHTML = `<div class="empty">${MARK}<b>${esc(t("empty_title"))}</b>
        <ol>${step(1, t("empty_s1"))}${step(2, t("empty_s2"))}${step(3, t("empty_s3"))}</ol>
        <button class="ghost" data-act="try-one">${esc(t("try_one"))}</button></div>`;
      return;
    }
    S.messages.forEach(upsertMessage);
    box.scrollTop = box.scrollHeight;
  }

  // ---------------------------------------------------------------- 聲音裝置（設定頁和第一次設定共用）

  function deviceNote(group, d) {
    const levels = (S.ui.levels || {})[group] || {};
    const level = levels[d.id];
    let note = level === null ? t("cannot_open") : (level > 0.12 ? t("has_sound") : t("no_sound"));
    if (d.default) note = t("win_default") + " · " + note;
    return note;
  }

  function devicesHTML() {
    const dv = S.ui.devices;
    if (!dv) return `<div class="note">${ICON.info}<span>${esc(t("dev_loading"))}</span></div>`;
    if (!dv.ok) return `<div class="note warn">${ICON.info}<span>${esc(t("dev_error", { error: dv.error }))}</span></div>`;
    const list = (group, items, chosen, cfgKey) => {
      if (!items.length) return `<div class="note warn">${ICON.info}<span>${esc(t("dev_none"))}</span></div>`;
      const hasChosen = chosen && items.some((d) => d.id === chosen);
      return items.map((d) => {
        const on = hasChosen ? d.id === chosen : d.default;
        return `<button class="device" data-act="device" data-key="${cfgKey}" data-id="${esc(d.id)}" data-name="${esc(d.name)}" aria-pressed="${on}">
          <span class="radio"><span></span></span>
          <span class="info"><span class="name">${esc(d.name)}</span><span class="dnote" data-note="${group}|${esc(d.id)}">${esc(deviceNote(group, d))}</span></span>
          <span class="bar" aria-hidden="true"><span data-level="${group}|${esc(d.id)}" style="width:0%"></span></span>
        </button>`;
      }).join("");
    };
    return `
      <section><div><h2>${esc(t("out_title"))}</h2><div class="sub">${esc(t("out_hint"))}</div></div>
        ${list("speakers", dv.speakers, S.cfg.speaker_device, "speaker")}</section>
      <section><div><h2>${esc(t("in_title"))}</h2><div class="sub">${esc(t("in_hint"))}</div></div>
        ${list("microphones", dv.microphones, S.cfg.mic_device, "mic")}
        ${micNoticeHTML() || `<div class="btn-row"><button class="ghost" data-act="find-mic">${esc(t("find_btn"))}</button>
          <span class="dim small">${esc(t("find_hint"))}</span></div>`}</section>
      <div class="note">${ICON.info}<span>${esc(t("dev_note"))}</span></div>`;
  }

  // 念出我的譯文：我說的、打的翻成對方的語言後，用 Windows 內建語音念到選的播放裝置（例如 VB-CABLE，Discord 就聽得到）
  function speakHTML() {
    const sp = S.speak || {};
    const dv = S.ui.devices || {};
    const chosen = S.cfg.speak_device || "";
    const opts = [["", t("speak_default")]].concat(((dv.ok && dv.speakers) || []).map((d) => [d.id, d.name]));
    if (chosen && !opts.some((o) => o[0] === chosen)) opts.push([chosen, S.cfg.speak_name || chosen]);
    const cable = ((dv.ok && dv.speakers) || []).some((d) => /cable input/i.test(d.name));
    let status = "";
    if (sp.available === false) status = `<div class="note warn">${ICON.info}<span>${esc(t("speak_unavailable"))}</span></div>`;
    else if (sp.error === "no_voice") status = `<div class="note warn">${ICON.info}<span>${esc(t("speak_no_voice", { lang: langName(sp.lang || S.cfg.their_lang) }))}</span></div>`;
    else if (sp.error) status = `<div class="note warn">${ICON.info}<span>${esc(t("n_speak_failed", { error: sp.error }))}</span></div>`;
    else if (sp.voice) status = `<div class="note">${ICON.check}<span>${esc(t("speak_voice", { voice: sp.voice }))}</span></div>`;
    return `<section id="speak-section"><div><h2>${esc(t("speak_title"))}</h2><div class="sub">${esc(t("speak_hint"))}</div></div>
        ${switchRow("speak_out", t("speak_on"), t("speak_on_desc"))}
        ${S.cfg.speak_out ? `<div class="setting-row choice"><div>${esc(t("speak_device"))}</div>
          <select id="speak-device" data-change="speak-device" aria-label="${esc(t("speak_device"))}">${opts.map(([v, n]) => `<option value="${esc(v)}" ${v === chosen ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
        <div class="dnote">${esc(t(cable ? "speak_cable_found" : "speak_cable_how"))}</div>
        <div class="btn-row"><button class="ghost" data-act="speak-test">${esc(t("speak_test"))}</button></div>${status}` : ""}
      </section>`;
  }

  function keyName(key) { return ["Mouse4", "Mouse5", "RightCtrl", "RightAlt"].indexOf(key) >= 0 ? t("key_" + key.toLowerCase()) : key; }

  function delayText() {
    const d = S.delay || {};
    return d.count ? t("delay_now", { n: d.count, hear: Number(d.hear).toFixed(1), translate: Number(d.translate).toFixed(1), total: (Number(d.hear) + Number(d.translate)).toFixed(1) })
      : t("delay_none");
  }

  function appName(exe) { return /\.exe$/i.test(exe) ? exe.slice(0, -4) : exe; }

  // 只聽指定程式的聲音：列出現在開著的程式，可以勾好幾個；一個都不勾就是聽整台電腦
  function appsHTML() {
    const a = S.ui.apps;
    if (!a) return "";
    const chosen = S.cfg.listen_apps || [];
    let body;
    if (!a.available) body = `<div class="note">${ICON.info}<span>${esc(t("apps_unavailable"))}</span></div>`;
    else {
      const rows = a.apps.map((p, i) => `<button class="device app" id="app-${i}" data-act="app" data-exe="${esc(p.exe)}" aria-pressed="${chosen.some((c) => c.toLowerCase() === p.exe.toLowerCase())}">
          <span class="box">${ICON.check}</span>
          <span class="info"><span class="name">${esc(p.name)}</span><span class="dnote">${esc(p.running ? p.title : t("apps_closed"))}</span></span>
        </button>`).join("");
      body = `${rows || `<div class="none">${esc(t("apps_none"))}</div>`}
        <div class="btn-row"><button class="ghost" data-act="apps-refresh">${esc(t("apps_refresh"))}</button>
          ${chosen.length ? `<button class="ghost" data-act="apps-clear">${esc(t("apps_clear"))}</button>` : ""}
          ${a.off && chosen.length ? "" : `<span class="dim small">${esc(chosen.length ? t("apps_now", { apps: chosen.map(appName).join("、") }) : t("apps_all"))}</span>`}</div>
        ${a.off && chosen.length ? `<div class="note warn">${ICON.info}<span>${esc(t("n_apps_audio_failed"))}</span></div>` : ""}`;
    }
    return `<section id="apps-section"><div><h2>${esc(t("apps_title"))}</h2><div class="sub">${esc(t("apps_hint"))}</div></div>${body}</section>`;
  }

  // 語音辨識用顯示卡（有 NVIDIA 顯示卡才出現）：選了之後照狀態顯示「要下載／下載中／已啟用／不行，已改回處理器」
  function speechGpuHTML() {
    const g = S.speechGpu || {};
    if (!g.can) return "";
    let note = "";
    if (S.cfg.speech_device === "gpu") {
      if (g.state === "need") note = `<div class="note warn">${ICON.info}<span>${esc(t("sgpu_need", { mb: g.mb }))}${g.error ? " " + esc(t("sgpu_error", { error: g.error })) : ""}</span>
        <button class="primary small" data-act="sgpu-download">${esc(t("sgpu_download"))}</button></div>`;
      else if (g.state === "downloading") note = `<div class="note">${ICON.info}<span id="sgpu-percent">${esc(t("sgpu_downloading", { percent: g.percent || 0 }))}</span>
        <button class="ghost small" data-act="sgpu-cancel">${esc(t("cancel"))}</button></div>`;
      else if (g.state === "failed") note = `<div class="note warn">${ICON.info}<span>${esc(t("sgpu_failed"))}${g.error ? " " + esc(t("sgpu_error", { error: g.error })) : ""}</span></div>`;
      else if (g.state === "ready") note = `<div class="note">${ICON.info}<span>${esc(t("sgpu_ready"))}</span></div>`;
    }
    return choiceRow("speech_device", t("speech_device"), [["cpu", t("speech_cpu")], ["gpu", t("speech_gpu")]]) + note;
  }

  // 聲音設定頁下半：辨識要快還是準、多小聲算說話、停多久算說完、回音、按住才說話（第一次設定的精靈不放這些）
  function voiceTuningHTML() {
    if (!S.ui.devices || !S.ui.devices.ok) return "";
    return `${appsHTML()}
      <section><div><h2>${esc(t("tune_title"))}</h2><div class="sub">${esc(t("tune_hint"))}</div></div>
        ${choiceRow("speech_mode", t("speech_mode"), [["fast", t("speech_fast")], ["accurate", t("speech_accurate")]])}
        ${speechGpuHTML()}
        <div class="note" id="delay-note">${ICON.info}<span>${esc(delayText())}</span></div>
        ${rangeRow("voice_sensitivity", t("sensitivity"), 1, 5, 1, RANGE_TEXT.voice_sensitivity)}
        ${rangeRow("end_silence", t("end_silence"), 0.3, 1.5, 0.1, RANGE_TEXT.end_silence)}
      </section>
      <section>
        ${switchRow("live_captions", t("live_captions"), t("live_captions_desc"))}
        ${switchRow("drop_echo", t("drop_echo"), t("drop_echo_desc"))}
        ${switchRow("ptt", t("ptt"), t("ptt_desc"))}
        ${S.cfg.ptt ? choiceRow("ptt_key", t("ptt_key"), (S.pttKeys || []).map((k) => [k, keyName(k)])) : ""}
      </section>
      ${speakersHTML()}`;
  }

  function paintLevels() {
    const levels = S.ui.levels;
    if (!levels || !S.ui.devices || !S.ui.devices.ok) return;
    document.querySelectorAll("[data-level]").forEach((el) => {
      const [group, id] = el.dataset.level.split("|");
      const v = (levels[group] || {})[id];
      el.style.width = (v ? Math.round(v * 100) : 0) + "%";
    });
    document.querySelectorAll("[data-note]").forEach((el) => {
      const [group, id] = el.dataset.note.split("|");
      const d = (S.ui.devices[group] || []).find((x) => x.id === id);
      if (d) el.textContent = deviceNote(group, d);
    });
  }

  async function openDevices() {
    S.ui.devices = null;
    S.ui.levels = null;
    refreshDevicePanels();
    S.ui.devices = await get("/api/devices") || { ok: false, error: "no response", speakers: [], microphones: [] };
    refreshDevicePanels();
    loadApps();
    post("/api/meters", { on: true });
  }

  // 打開某個設定分頁時要另外去拿的資料：聲音頁要裝置和音量，聲音頁和螢幕頁都要開著的程式清單
  function tabOpened() {
    if (S.ui.tab === "audio") openDevices();
    if (S.ui.tab === "screen" || S.ui.tab === "quality") loadApps();
    if (S.ui.tab === "overlay") loadScreens();
    if (S.ui.tab === "history") { S.ui.hist = null; S.ui.recapList = null; loadHistory(); loadRecaps(); }
    if (S.ui.tab === "learn") { S.ui.review = null; loadWords(); }
    if (S.ui.tab === "health") { S.ui.useAsk = false; loadHealth(); loadUsage(); }
    if (S.ui.tab === "storage") { S.ui.storeAsk = ""; loadStorage(); }
    if (S.ui.tab === "help") { S.ui.friendSaved = null; loadHelp(); }
  }

  async function loadApps() {
    S.ui.apps = await get("/api/apps") || null;
    if (S.view === "settings" && (S.ui.tab === "audio" || S.ui.tab === "screen" || S.ui.tab === "quality")) renderSettingsPage();
  }

  function refreshDevicePanels() {
    if (S.view === "settings" && S.ui.tab === "audio") renderSettingsPage();
    if (S.view === "wizard" && S.ui.wz === 1) renderWizard();
  }

  // ---------------------------------------------------------------- 設定

  const TABS = [["history", "nav_history", "clock"], ["learn", "nav_learn", "book"], ["overlay", "nav_overlay", "chat"], ["audio", "nav_audio", "listen"], ["quality", "nav_quality", "quality"], ["screen", "nav_screen", "screen"], ["general", "nav_general", "globe"], ["storage", "nav_storage", "disk"], ["health", "nav_health", "shield"], ["help", "nav_help", "help"], ["about", "nav_about", "info"]];

  // 固定在左邊的導覽：對話和每一頁設定都在同一層，視窗不用為了設定變大變小
  function renderRail() {
    const item = (to, key, icon) => {
      const here = to === "main" ? S.view === "main" : (S.view === "settings" && S.ui.tab === to);
      return `<button class="rail-item" data-act="nav" data-to="${to}" ${here ? 'aria-current="page"' : ""} title="${esc(t(key))}">${ICON[icon]}<span>${esc(t(key))}</span></button>`;
    };
    $("rail").innerHTML = `
      <div class="rail-brand">${MARK}<div class="wordmark">${WORDMARK}</div></div>
      ${item("main", "nav_chat", "bubble")}
      ${item("history", "nav_history", "clock")}
      ${item("learn", "nav_learn", "book")}
      <div class="rail-title">${esc(t("settings"))}</div>
      ${TABS.filter(([id]) => id !== "about" && id !== "help" && id !== "history" && id !== "learn").map(([id, key, icon]) => item(id, key, icon)).join("")}
      <div class="grow"></div>
      ${item("help", "nav_help", "help")}
      ${item("about", "nav_about", "info")}`;
  }

  function switchRow(key, label, desc) {
    return `<div class="setting-row"><div><div>${esc(label)}</div>${desc ? `<div class="desc">${esc(desc)}</div>` : ""}</div>
      <button class="switch" role="switch" data-act="toggle" data-key="${key}" aria-checked="${S.cfg[key] ? "true" : "false"}" aria-label="${esc(label)}"><span></span></button></div>`;
  }

  function gb(mb) { return (Math.round((mb || 0) / 102.4) / 10).toFixed(1).replace(/\.0$/, ""); }

  function qualityPage() {
    const tier = S.gpu.tier || "cpu";
    const high = S.mode.quality === "high";
    const gpuLine = S.gpu.name
      ? (tier === "high" ? t("gpu_found", { name: S.gpu.name, gb: gb(S.gpu.vram_mb) }) : t("gpu_small", { name: S.gpu.name, gb: gb(S.gpu.vram_mb) }))
      : t("gpu_none");
    // 語氣的說明照「會翻成哪個語言」寫：韓文、日文有明確的說法（半語／タメ口、해요體／です・ます）
    const toneOf = ["ko", "ja"].find((l) => S.cfg.their_lang === l) || ["ko", "ja"].find((l) => S.cfg.my_lang === l) || "";
    const tk = (key) => t(toneOf ? `${key}_${toneOf}` : key);
    const game = S.game || {};
    const gameNames = {};
    (game.own || []).forEach((g) => { gameNames[g.exe] = g.name; });
    if (game.exe) gameNames[game.exe] = game.name;
    const useGames = S.cfg.game_terms && S.cfg.game_auto;      // 遊戲專用詞只有開著自動切換時才有用
    const rows = S.glossary.length
      ? S.glossary.map((g) => `<div class="grow-row"><span>${esc(g.src)}</span><span>${esc(g.dst)}${g.game ? ` <span class="tag plain game-tag">${esc(gameNames[g.game] || g.game)}</span>` : ""}</span>
          <button data-act="gloss-remove" data-src="${esc(g.src)}" data-game="${esc(g.game || "")}" aria-label="${esc(t("remove", { word: g.src }))}">${ICON.x}</button></div>`).join("")
      : `<div class="none">${esc(t("gloss_empty"))}</div>`;
    // 「用在」的選項：所有遊戲、現在認出來的遊戲、已經有專用詞的遊戲、現在開著的程式（不在清單上的遊戲也能自己挑）
    const gameOpts = [["", t("gloss_game_all")]];
    const seenOpt = new Set([""]);
    const addOpt = (exe, name) => { const k = (exe || "").toLowerCase(); if (!seenOpt.has(k)) { seenOpt.add(k); gameOpts.push([k, name]); } };
    if (game.exe) addOpt(game.exe, game.name);
    (game.own || []).forEach((g) => addOpt(g.exe, g.name));
    ((S.ui.apps && S.ui.apps.apps) || []).filter((a) => a.running).forEach((a) => addOpt(a.exe, a.name));
    const pick = S.ui.glossGame !== undefined && seenOpt.has(S.ui.glossGame) ? S.ui.glossGame : (game.exe || "");
    const taught = S.lessons.length
      ? S.lessons.slice().reverse().map((l) => `<div class="grow-row"><span>${esc(l.src)}</span><span>${esc(l.dst)}</span>
          <button data-act="lesson-remove" data-src="${esc(l.src)}" data-target="${esc(l.target)}" aria-label="${esc(t("remove", { word: l.src }))}">${ICON.x}</button></div>`).join("")
      : `<div class="none">${esc(t("lessons_empty"))}</div>`;
    return `
      <section>
        <div><h2>${esc(t("engine_title"))}</h2><div class="sub">${esc(t("engine_hint"))}</div></div>
        <div class="quality">
          <button class="qcard" data-act="quality" data-q="standard" aria-pressed="${!high}">
            <span class="head"><span class="name">${esc(t("q_standard"))}</span>${!high ? `<span class="tag plain">${esc(t("in_use"))}</span>` : ""}</span>
            <span class="desc">${esc(t("q_standard_desc"))}</span>
          </button>
          <button class="qcard" data-act="quality" data-q="auto" aria-pressed="${high}" ${tier === "high" ? "" : "disabled"}>
            <span class="head"><span class="name">${esc(t("q_high"))}</span><span class="tag">${esc(high ? t("in_use") : t("uses_gpu"))}</span></span>
            <span class="desc">${esc(t("q_high_desc"))}</span>
          </button>
        </div>
        <div class="note ${S.fallback ? "warn" : ""}">${ICON.info}<span>${esc(S.fallback ? t("gpu_fallback") : gpuLine)}</span></div>
        ${choiceRow("engine_rest", t("engine_rest"), ["0", "10", "30", "60"].map((m) => [m, t("engine_rest_" + m)]))}
        <div class="dnote">${esc(t("engine_rest_desc"))}</div>
      </section>
      <section>
        <h2>${esc(tk("tone_title"))}</h2>
        <div class="pills">
          <button class="pill tall" data-act="tone" data-tone="casual" aria-pressed="${S.cfg.tone === "casual"}">${esc(tk("tone_casual"))}</button>
          <button class="pill tall" data-act="tone" data-tone="polite" aria-pressed="${S.cfg.tone === "polite"}">${esc(tk("tone_polite"))}</button>
        </div>
        <div class="setting-row choice"><div>${esc(t("tone_keep_title"))}</div><div class="pills">
          <button class="pill" data-act="tone-keep" data-value="" aria-pressed="${!S.cfg.tone_keep}">${esc(t("tone_keep_off"))}</button>
          <button class="pill" data-act="tone-keep" data-value="1" aria-pressed="${!!S.cfg.tone_keep}">${esc(t("tone_keep_on"))}</button></div></div>
        <div class="dnote">${esc(t(S.cfg.tone_keep ? "tone_keep_on_desc" : "tone_keep_off_desc"))}</div>
      </section>
      <section>${switchRow("use_context", t("use_context"), t("use_context_desc"))}
        ${switchRow("back_translate", t("back_translate"), t("back_translate_desc"))}</section>
      <section>${switchRow("game_terms", t("terms_title"), t("terms_desc"))}
        ${S.cfg.game_terms ? `<div class="packs">${["lol", "chat"].map((p) => `<button class="pack" id="pack-${p}" data-act="pack" data-pack="${p}" aria-pressed="${(S.cfg.term_packs || []).indexOf(p) >= 0}">
          <span class="name">${ICON.check}${esc(t("pack_" + p))}</span><span class="desc">${esc(t("pack_" + p + "_desc"))}</span></button>`).join("")}</div>
          ${switchRow("game_auto", t("game_auto"), t("game_auto_desc"))}
          ${S.cfg.game_auto ? `<div class="note" id="game-now">${ICON.info}<span>${esc(game.exe ? t("game_now", { game: game.name }) : t("game_none"))}</span></div>` : ""}` : ""}</section>
      <section>
        <div><h2>${esc(t("gloss_title"))}</h2><div class="sub">${esc(t("gloss_hint"))}</div></div>
        <form class="gloss-form" id="gloss-form">
          <input name="src" aria-label="${esc(t("gloss_src"))}" placeholder="${esc(t("gloss_src"))}" maxlength="80">
          <input name="dst" aria-label="${esc(t("gloss_dst"))}" placeholder="${esc(t("gloss_dst"))}" maxlength="80">
          <button class="primary" type="submit">${esc(t("gloss_add"))}</button>
        </form>
        ${useGames ? `<div class="setting-row choice gloss-game"><div><div>${esc(t("gloss_game"))}</div><div class="desc">${esc(t("gloss_game_desc"))}</div></div>
          <select id="gloss-game" aria-label="${esc(t("gloss_game"))}">${gameOpts.map(([v, n]) => `<option value="${esc(v)}" ${v === pick ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>` : ""}
        <div class="gloss"><div class="grow-head"><span>${esc(t("gloss_src"))}</span><span>${esc(t("gloss_dst"))}</span><span></span></div>${rows}</div>
      </section>
      <section>
        <div><h2>${esc(t("lessons_title"))}</h2><div class="sub">${esc(t("lessons_hint"))}</div></div>
        <div class="gloss"><div class="grow-head"><span>${esc(t("lessons_src"))}</span><span>${esc(t("lessons_dst"))}</span><span></span></div>${taught}</div>
      </section>
      ${memorySection()}
      <section>
        <div><h2>${esc(t("lib_title"))}</h2><div class="sub">${esc(t("lib_hint"))}</div></div>
        <div class="btn-row">
          <button class="ghost" data-act="lib-export">${esc(t("lib_export"))}</button>
          <button class="ghost" data-act="lib-import">${esc(t("lib_import"))}</button>
          <input type="file" id="lib-file" accept=".json,application/json" hidden>
        </div>
      </section>`;
  }

  // 翻過的句子：同一句不用重翻；匯出分享檔給朋友、匯入朋友的（一包一包，可以整包刪）
  function memorySection() {
    const m = S.memory || { mine: 0, fixed: 0, packs: [] };
    const range = S.ui.memRange || "2h";
    const packs = (m.packs || []).length
      ? m.packs.map((p) => `<div class="grow-row"><span>${esc(p.name)}</span><span>${esc(t("mem_pack_count", { n: p.count }))}</span>
          <button data-act="mem-pack-remove" data-pack="${esc(p.id)}" aria-label="${esc(t("remove", { word: p.name }))}">${ICON.x}</button></div>`).join("")
      : `<div class="none">${esc(t("mem_packs_empty"))}</div>`;
    const forget = S.ui.memAsk
      ? `<div class="note warn" id="mem-confirm">${ICON.info}<span>${esc(t("mem_forget_ask", { n: m.mine - m.fixed }))}</span>
          <button class="danger" data-act="mem-forget-yes">${esc(t("mem_forget_yes"))}</button><button class="ghost" data-act="mem-forget-no">${esc(t("cancel"))}</button></div>`
      : "";
    return `<section id="mem-section">
        <div><h2>${esc(t("mem_title"))}</h2><div class="sub">${esc(t("mem_hint"))}</div></div>
        ${switchRow("remember_translations", t("mem_remember"), t("mem_count", { n: m.mine, fixed: m.fixed }))}
        ${switchRow("use_shared", t("mem_shared"), t("mem_shared_desc"))}
        <div class="setting-row choice"><div>${esc(t("mem_range"))}</div><div class="pills">${[["2h", "mem_r2h"], ["today", "mem_rtoday"], ["all", "mem_rall"]].map(([v, k]) =>
          `<button class="pill" data-act="mem-range" data-value="${v}" aria-pressed="${range === v}">${esc(t(k))}</button>`).join("")}</div></div>
        <div class="setting-row choice"><div><div>${esc(t("mem_with_mine"))}</div><div class="desc">${esc(t("mem_with_mine_desc"))}</div></div>
          <button class="switch" role="switch" data-act="mem-with-mine" aria-checked="${S.ui.memWithMine ? "true" : "false"}" aria-label="${esc(t("mem_with_mine"))}"><span></span></button></div>
        <div class="btn-row">
          <button class="ghost" data-act="mem-export" ${m.mine ? "" : "disabled"}>${esc(t("mem_export"))}</button>
          <button class="ghost" data-act="mem-import">${esc(t("mem_import"))}</button>
          <button class="ghost" data-act="sub-import">${esc(t("sub_import"))}</button>
          <button class="ghost" data-act="mem-forget" ${m.mine - m.fixed > 0 ? "" : "disabled"}>${esc(t("mem_forget"))}</button>
          <input type="file" id="mem-file" accept=".json,application/json" hidden>
          <input type="file" id="sub-file" accept=".srt,.vtt,text/vtt" multiple hidden>
        </div>${forget}${subtitlePreview()}
        <div class="gloss"><div class="grow-head"><span>${esc(t("mem_packs"))}</span><span></span><span></span></div>${packs}</div>
        ${worksBlock(m)}
      </section>`;
  }

  // 匯入字幕檔：先看配出來的樣子（哪種語言翻成哪種、前 5 句），選記在哪一部，再匯入
  function subtitlePreview() {
    const p = S.ui.subPreview;
    if (!p) return "";
    const now = p.now;
    const pick = S.ui.subWork != null ? S.ui.subWork : (now ? now.key : "");
    const sample = p.sample.map(([a, b]) => `<div class="sub-pair"><div>${esc(a)}</div><div class="dim">${esc(b)}</div></div>`).join("");
    return `<div class="sub-preview" id="sub-preview">
        <div><b>${esc(t("sub_found", { count: p.count, from: langName(p.lang), to: langName(p.target) }))}</b></div>
        <div class="sub-sample">${sample}</div>
        <div class="btn-row"><button class="ghost" data-act="sub-swap">${esc(t("sub_swap"))}</button></div>
        <div class="setting-row choice"><div>${esc(t("sub_where"))}</div><div class="pills">
          ${now ? `<button class="pill" data-act="sub-work" data-work="${esc(now.key)}" aria-pressed="${pick === now.key}">${esc(t("sub_now", { title: now.title }))}</button>` : ""}
          <button class="pill" data-act="sub-work" data-work="" aria-pressed="${pick === ""}">${esc(t("sub_new"))}</button></div></div>
        ${pick === "" ? `<input id="sub-title" maxlength="120" value="${esc(S.ui.subName || "")}" aria-label="${esc(t("sub_title"))}" placeholder="${esc(t("sub_title"))}">` : ""}
        <div class="note">${ICON.info}<span>${esc(t("sub_rights"))}</span></div>
        <div class="btn-row"><button class="primary" data-act="sub-yes">${esc(t("sub_yes"))}</button><button class="ghost" data-act="sub-no">${esc(t("cancel"))}</button></div>
      </div>`;
  }

  async function subtitleLoad(swap) {
    const r = await post("/api/memory", { action: "subtitles_preview", files: S.ui.subFiles, swap });
    if (!r || !r.ok) {
      S.ui.subPreview = null;
      toast(t("n_" + ((r && r.error) || "sub_unreadable")), 9000, "warn");
    } else {
      S.ui.subPreview = r;
      S.ui.subSwap = swap;
    }
    renderSettingsPage();
    const b = document.querySelector(S.ui.subPreview ? '[data-act="sub-swap"]' : '[data-act="sub-import"]'); if (b) b.focus();
  }

  // 記過的作品（哪一部影片、哪一個網頁）：收合起來，打開才看得到；每一部可以單獨匯出、不用別人的、忘掉
  function worksBlock(m) {
    const list = m.works || [];
    const rows = list.length ? list.map((w) => {
      const naming = S.ui.workExport === w.key
        ? `<div class="work-export"><input id="work-title" maxlength="120" value="${esc(w.title)}" aria-label="${esc(t("works_name"))}">
            <button class="primary" data-act="work-export-yes" data-work="${esc(w.key)}">${esc(t("works_export_yes"))}</button>
            <button class="ghost" data-act="work-export-no">${esc(t("cancel"))}</button>
            <div class="desc">${esc(t("works_export_desc", { n: w.mine }))}</div></div>` : "";
      return `<div class="work-row"><div class="work-name"><div>${esc(w.title)}</div>
          <div class="desc">${esc(t("works_count", { mine: w.mine, shared: w.shared }))}</div></div>
          <div class="work-acts">
            <button class="ghost" data-act="work-export" data-work="${esc(w.key)}" ${w.mine ? "" : "disabled"}>${esc(t("works_export"))}</button>
            <button class="pill" data-act="work-own" data-work="${esc(w.key)}" aria-pressed="${w.own_only}" title="${esc(t("works_own_desc"))}">${esc(t("works_own"))}</button>
            <button data-act="work-forget" data-work="${esc(w.key)}" aria-label="${esc(t("remove", { word: w.title }))}">${ICON.x}</button>
          </div>${naming}</div>`;
    }).join("") : `<div class="none">${esc(t("works_empty"))}</div>`;
    return `${switchRow("remember_works", t("works_remember"), t("works_remember_desc"))}
      ${switchRow("use_shown_translation", t("shown_title"), t("shown_desc"))}
      <div class="works"><button class="works-toggle" data-act="works-open" aria-expanded="${S.ui.worksOpen ? "true" : "false"}"><span aria-hidden="true">${S.ui.worksOpen ? "▾" : "▸"}</span><span>${esc(t("works_title", { n: list.length }))}</span></button>
        ${S.ui.worksOpen ? `<div class="works-list">${rows}</div>` : ""}</div>`;
  }

  // ---------------------------------------------------------------- 螢幕上的聊天區（可以好幾個）

  function regions() { return (S.cfg && S.cfg.regions) || []; }

  function regionStats(id) {
    const n = (S.screenStats || {})[id];
    if (!n || !n.lines) return t("rstat_none");
    return t("rstat", { lines: n.lines, sent: n.sent, skipped: n.lines - n.sent, repeat: n.repeat + n.old, other: n.other });
  }

  function ocrResultHTML() {
    const o = S.ui.ocr;
    if (o === "reading") return `<div class="note">${ICON.info}<span>${esc(t("ocr_reading"))}</span></div>`;
    if (o && !o.ok) return `<div class="note warn">${ICON.info}<span>${esc(o.error === "app_closed" ? t("n_region_app_closed", { app: o.app }) : t("ocr_failed", { error: o.error }))}</span></div>`;
    if (o && !o.lines.length) return `<div class="note">${ICON.info}<span>${esc(t("ocr_nothing"))}</span></div>`;
    if (o) return `<div class="ocr-lines">${o.lines.map((l) => `<div>${esc(l)}</div>`).join("")}</div>`;
    return "";
  }

  function regionCard(r, i) {
    const open = ((S.ui.apps && S.ui.apps.apps) || []).filter((a) => a.running);
    const options = [["", t("region_fixed")]].concat(open.map((a) => [a.exe, a.name]));
    // 選過的程式現在沒開：還是要列出來，不然下拉選單會顯示成別的
    if (r.app && !options.some((o) => o[0].toLowerCase() === r.app.toLowerCase())) options.push([r.app, `${appName(r.app)}（${t("apps_closed")}）`]);
    const label = r.name || t("region_n", { n: i + 1 });
    return `<div class="region-card" id="region-${esc(r.id)}" data-on="${r.on ? "true" : "false"}">
        <div class="rhead">
          <input class="rname" type="text" data-change="region-name" data-id="${esc(r.id)}" value="${esc(r.name || "")}" placeholder="${esc(t("region_n", { n: i + 1 }))}" maxlength="30" aria-label="${esc(t("region_name"))}">
          <button class="switch" role="switch" data-act="region-on" data-id="${esc(r.id)}" aria-checked="${r.on ? "true" : "false"}" aria-label="${esc(t("region_on"))}：${esc(label)}"><span></span></button>
        </div>
        <div class="dnote">${esc(t("region_size", { w: r.width, h: r.height, x: r.left, y: r.top }))}</div>
        <div class="rfollow"><label for="rapp-${esc(r.id)}">${esc(t("region_follow"))}</label>
          <select id="rapp-${esc(r.id)}" data-change="region-app" data-id="${esc(r.id)}">${options.map((o) => `<option value="${esc(o[0])}" ${o[0].toLowerCase() === (r.app || "").toLowerCase() ? "selected" : ""}>${esc(o[1])}</option>`).join("")}</select></div>
        <div class="dnote" id="rstat-${esc(r.id)}">${esc(regionStats(r.id))}</div>
        <div class="btn-row">
          <button class="ghost small" data-act="region" data-id="${esc(r.id)}" data-keep="1">${esc(t("region_change"))}</button>
          <button class="ghost small" data-act="ocr-test" data-id="${esc(r.id)}">${esc(t("ocr_test"))}</button>
          <button class="ghost small" data-act="region-remove" data-id="${esc(r.id)}">${esc(t("region_remove"))}</button>
        </div>
        ${S.ui.ocrId === r.id ? ocrResultHTML() : ""}
      </div>`;
  }

  function screenPage() {
    const list = regions();
    const full = list.length >= 6;
    return `
      <section id="regions-section">
        <div><h2>${esc(t("screen_title"))}</h2><div class="sub">${esc(t("screen_hint"))}</div></div>
        ${list.length ? `<div class="sub">${esc(t("regions_hint"))}</div>${list.map(regionCard).join("")}
          <div class="btn-row"><button class="ghost" data-act="region-add" ${full ? "disabled" : ""}>${ICON.frame}${esc(t("region_add"))}</button>
            <span class="dim small">${esc(t("regions_max", { max: 6 }))}</span></div>
          <div class="sub">${esc(t("ocr_test_hint"))}</div>
          <div class="note">${ICON.info}<span>${esc(t("region_follow_hint"))}</span></div>`
        : `<div class="note">${ICON.info}<span>${esc(t("region_none"))}</span></div>
          <div class="btn-row"><button class="primary big" data-act="region">${ICON.frame}${esc(t("region_pick"))}</button></div>`}
      </section>
      <section>${switchRow("also_english", t("also_english"))}</section>`;
  }

  // 區域的名字、開關、跟著哪個程式、刪除：都是把整份清單送回去存（位置和大小只有框選畫面能改）
  async function saveRegions(list, mark) {
    const r = await post("/api/config", { regions: list });
    if (!(r && r.ok && r.saved)) toast(t("n_save_failed", { error: (r && r.error) || "" }), 8000, "error");
    else if (r.changed.indexOf("regions") >= 0) flashSaved(mark);
    if (r && r.config) S.cfg = r.config;
    if (S.view === "settings") renderSettingsPage();      // 沒存成（例如要跟的程式沒開）也要畫回原本的樣子
    renderSources();
  }

  function rangeRow(key, label, min, max, step, text) {
    const v = Number(S.cfg[key]);
    return `<div class="range-row"><label for="rng-${key}">${esc(label)}</label>
      <input type="range" id="rng-${key}" data-change="${key}" data-range="${key}" min="${min}" max="${max}" step="${step}" value="${v}">
      <output id="out-${key}">${esc(text(v))}</output></div>`;
  }

  const RANGE_TEXT = {
    overlay_font: (v) => String(v),
    overlay_seconds: (v) => (v > 0 ? t("ov_seconds_value", { n: v }) : t("ov_forever")),
    overlay_backdrop: (v) => (v > 0 ? v + "%" : t("ov_backdrop_none")),
    overlay_outline: (v) => (v > 0 ? String(v) : t("ov_outline_none")),
    overlay_opacity: (v) => v + "%",
    overlay_lines: (v) => (v > 0 ? t("ov_lines_value", { n: v }) : t("ov_lines_all")),
    voice_sensitivity: (v) => (v <= 1 ? t("sens_low") : v >= 5 ? t("sens_high") : v === 3 ? t("sens_mid") : String(v)),
    end_silence: (v) => t("pause_value", { s: Number(v).toFixed(1) })
  };

  const OV_COLORS = ["#FFFFFF", "#FFE98A", "#9FF5C9", "#9AD8FF", "#FFB3C7"];
  function choiceRow(key, label, options) {      // 一列選項，一次只能選一個
    return `<div class="setting-row choice"><div>${esc(label)}</div><div class="pills">${options.map(([value, text]) =>
      `<button class="pill" data-act="set" data-key="${key}" data-value="${value}" aria-pressed="${String(S.cfg[key]) === value}">${esc(text)}</button>`).join("")}</div></div>`;
  }
  function colorRow() {
    const now = String(S.cfg.overlay_color || "#FFFFFF").toUpperCase();
    return `<div class="setting-row choice"><div>${esc(t("ov_color"))}</div><div class="swatches small">
      ${OV_COLORS.map((c) => `<button class="swatch" data-act="set" data-key="overlay_color" data-value="${c}" style="--sw:${c}" aria-pressed="${now === c}" aria-label="${esc(t("ov_color"))} ${c}"></button>`).join("")}
      <label class="color-pick" title="${esc(t("ov_color_custom"))}"><input type="color" id="col-overlay_color" data-change="overlay_color" data-live="overlay_color" value="${esc(now)}" aria-label="${esc(t("ov_color_custom"))}"></label></div></div>`;
  }
  function nameColorsRow() {
    const colors = S.cfg.overlay_name_colors || {};
    return `<div class="setting-row choice"><div>${esc(t("ov_name_colors"))}</div><div class="name-colors">
      ${["heard", "screen", "me"].map((who) => `<label class="color-pick"><input type="color" id="col-name-${who}" data-change="overlay_name_colors" data-sub="${who}" data-live="overlay_name_colors" value="${esc(colors[who] || "#FFFFFF")}"><span>${esc(t("ov_name_" + who))}</span></label>`).join("")}</div></div>`;
  }

  // 顯示方式：浮在畫面上 / 底部字幕條 / 只在視窗裡（三種共用下面同一組外觀設定）
  function displayMode() { return !S.cfg.overlay ? "window" : (S.cfg.overlay_form === "bar" ? "bar" : "float"); }

  // 縮小的螢幕圖：每個螢幕一塊，留言是上面的方框。位置都用百分比，所以不用量畫面大小。
  function mapBounds() {
    const ms = S.ui.screens.monitors;
    const x0 = Math.min(...ms.map((m) => m.left)), y0 = Math.min(...ms.map((m) => m.top));
    const x1 = Math.max(...ms.map((m) => m.left + m.width)), y1 = Math.max(...ms.map((m) => m.top + m.height));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  function mapStyle(r, b) {
    return `left:${((r[0] - b.x) * 100 / b.w).toFixed(3)}%;top:${((r[1] - b.y) * 100 / b.h).toFixed(3)}%;width:${(r[2] * 100 / b.w).toFixed(3)}%;height:${(r[3] * 100 / b.h).toFixed(3)}%`;
  }
  function mapWhere(r) { return t("map_where", { x: r[0], y: r[1], w: r[2], h: r[3] }); }
  function mapHTML() {
    const sc = S.ui.screens, mode = displayMode();
    if (!sc || !sc.monitors || mode === "window") return "";
    const b = mapBounds(), bar = mode === "bar";
    return `<section><div><h2>${esc(t("map_title"))}</h2><div class="sub">${esc(t(bar ? "map_hint_bar" : "map_hint_float"))}</div></div>
        <div class="screen-map" id="screen-map" style="aspect-ratio:${b.w} / ${b.h}">
          ${sc.monitors.map((m, i) => `<button class="mon" data-act="map-monitor" data-code="${i}" style="${mapStyle([m.left, m.top, m.width, m.height], b)}" aria-pressed="${sc.monitor === i}"
            aria-label="${esc(t("map_screen_pick", { n: i + 1, w: m.width, h: m.height }))}" title="${esc(t("map_screen_pick", { n: i + 1, w: m.width, h: m.height }))}"><span>${esc(t("map_screen", { n: i + 1 }))}</span><small>${m.width}×${m.height}</small></button>`).join("")}
          <div class="map-box${bar ? " bar" : ""}" id="map-box" style="${mapStyle(sc.rect, b)}" ${bar ? "" : `tabindex="0" role="group" aria-label="${esc(t("map_box"))}"`}>${bar ? "" : '<i class="grip"></i>'}</div>
        </div>
        <div class="dim small" id="map-where">${esc(mapWhere(sc.rect))}</div>
        ${bar ? "" : `<div class="btn-row">
          <button class="ghost" data-act="overlay-edit">${ICON.frame}${esc(S.overlay.editing ? t("ov_done") : t("ov_move"))}</button>
          <button class="ghost" data-act="overlay-reset">${esc(t("ov_reset"))}</button></div>`}
      </section>`;
  }
  function paintMapBox() {      // 只換方框的位置（真的留言被拖動時一秒會來很多次，不重畫整頁）
    const sc = S.ui.screens, box = $("map-box"), where = $("map-where");
    if (!sc || !box) return;
    box.style.cssText = mapStyle(sc.rect, mapBounds());
    if (where) where.textContent = mapWhere(sc.rect);
  }
  async function loadScreens() {
    S.ui.screens = await get("/api/screens") || null;
    if (S.view === "settings" && S.ui.tab === "overlay" && !S.ui.mapDrag) renderSettingsPage();
  }
  let mapTimer = null, mapSent = 0;
  function mapPlace(rect, final) {      // 拖的時候一秒最多送十幾次；放開那一次才存，存好顯示「已儲存」
    S.ui.screens.rect = rect;
    paintMapBox();
    clearTimeout(mapTimer);
    const send = async () => {
      mapSent = Date.now();
      const r = await post("/api/overlay", { rect, final });
      if (final && r && r.ok) {
        if (r.rect) { S.ui.screens.rect = r.rect; paintMapBox(); }
        if (r.saved) flashSaved("overlay_map"); else toast(t("n_save_failed", { error: "" }), 8000, "error");
      }
    };
    if (final || Date.now() - mapSent > 70) send(); else mapTimer = setTimeout(send, 70);
  }

  function layoutsHTML() {
    const list = S.cfg.overlay_layouts || [];
    return `<section id="layouts-section"><div><h2>${esc(t("lay_mine"))}</h2><div class="sub">${esc(t("lay_hint", { max: 5 }))}</div></div>
        ${list.length ? `<div class="layouts">${list.map((l, i) => `<div class="layout-item" id="layout-${i}">
          <button class="qcard" data-act="layout-apply" data-code="${i}"><span class="head"><span class="name">${esc(l.name)}</span></span>
            <span class="desc">${esc(t("mode_" + (l.form === "bar" ? "bar" : "float")))} · ${esc(t("lay_apply"))}</span></button>
          <button class="icon-btn" data-act="layout-remove" data-code="${i}" aria-label="${esc(t("lay_remove", { name: l.name }))}" title="${esc(t("lay_remove", { name: l.name }))}">${ICON.x}</button></div>`).join("")}</div>` : ""}
        <form class="btn-row" id="layout-form"><input type="text" id="layout-name" name="name" maxlength="24" placeholder="${esc(t("lay_name"))}" aria-label="${esc(t("lay_name"))}">
          <button class="ghost" type="submit">${esc(t("lay_save"))}</button></form>
      </section>`;
  }

  function overlayPage() {
    const mode = displayMode(), on = mode !== "window", bar = mode === "bar";
    return `
      <section>
        <div><h2>${esc(t("mode_title"))}</h2><div class="sub">${esc(t("mode_hint"))}</div></div>
        <div class="quality presets" id="display-mode">${["float", "bar", "window"].map((id) => `<button class="qcard" data-act="display-mode" data-code="${id}" aria-pressed="${mode === id}">
          <span class="head"><span class="name">${esc(t("mode_" + id))}</span></span><span class="desc">${esc(t("mode_" + id + "_desc"))}</span></button>`).join("")}</div>
        <div class="btn-row"><button class="ghost" data-act="overlay-test" ${on ? "" : "disabled"}>${esc(t("ov_test"))}</button></div>
        ${on ? `<div class="note">${ICON.info}<span>${esc(t("ov_game_note"))}</span></div>` : ""}
        ${switchRow("share_visible", t("share_visible"), t("share_visible_desc"))}
      </section>
      ${streamHTML()}
      ${mapHTML()}
      <section><div><h2>${esc(t("ov_presets"))}</h2><div class="sub">${esc(t("ov_presets_hint"))}</div></div>
        <div class="quality presets">${["chat", "subtitle", "quiet"].map((id) => `<button class="qcard" data-act="overlay-preset" data-code="${id}">
          <span class="head"><span class="name">${esc(t("ov_preset_" + id))}</span></span><span class="desc">${esc(t("ov_preset_" + id + "_desc"))}</span></button>`).join("")}</div>
      </section>
      ${layoutsHTML()}
      <figure class="ov-preview"><img id="ov-preview" alt="${esc(t("ov_preview"))}" ${S.ui.preview ? `src="${S.ui.preview}"` : ""}>
        <figcaption>${esc(t("ov_preview"))}</figcaption></figure>
      <section><h2>${esc(t("ov_text_title"))}</h2>
        ${rangeRow("overlay_font", t("ov_font"), 14, 60, 1, RANGE_TEXT.overlay_font)}
        ${choiceRow("overlay_family", t("ov_family"), [["sans", t("ov_family_sans")], ["serif", t("ov_family_serif")], ["kai", t("ov_family_kai")]])}
        ${switchRow("overlay_bold", t("ov_bold"))}
        ${colorRow()}
        ${rangeRow("overlay_outline", t("ov_outline"), 0, 4, 1, RANGE_TEXT.overlay_outline)}
        ${rangeRow("overlay_backdrop", t("ov_backdrop"), 0, 80, 5, RANGE_TEXT.overlay_backdrop)}
        ${rangeRow("overlay_opacity", t("ov_opacity"), 30, 100, 5, RANGE_TEXT.overlay_opacity)}
      </section>
      <section><h2>${esc(t("ov_names_title"))}</h2>
        ${switchRow("overlay_names", t("ov_names"))}
        ${nameColorsRow()}
      </section>
      <section><h2>${esc(t("ov_layout_title"))}</h2>
        ${bar ? "" : choiceRow("overlay_align", t("ov_align"), [["left", t("ov_align_left")], ["center", t("ov_align_center")], ["right", t("ov_align_right")]])}
        ${bar ? "" : choiceRow("overlay_grow", t("ov_grow"), [["up", t("ov_grow_up")], ["down", t("ov_grow_down")]])}
        ${bar ? "" : rangeRow("overlay_lines", t("ov_lines"), 0, 12, 1, RANGE_TEXT.overlay_lines)}
        ${rangeRow("overlay_seconds", t("ov_seconds"), 0, 60, 1, RANGE_TEXT.overlay_seconds)}
        <div class="sub">${esc(t("ov_seconds_hint"))}</div>
        ${switchRow("overlay_original", t("ov_original"))}
        ${switchRow("overlay_mine", t("ov_mine"))}
      </section>
      <section><div class="btn-row"><button class="ghost" data-act="overlay-look-reset">${esc(t("ov_look_reset"))}</button></div></section>`;
  }

  // 直播模式：OBS 的「瀏覽器來源」貼這個網址，就有乾淨的雙語字幕（網址固定，重開也不變）
  function streamHTML() {
    const st = S.stream || {};
    const on = !!S.cfg.stream;
    let body = "";
    if (on && st.url) {
      body = `<div class="stream-url"><code id="stream-url">${esc(st.url)}</code>
          <button class="ghost" data-act="stream-copy" id="stream-copy">${esc(S.ui.streamCopied ? t("stream_copied") : t("stream_copy"))}</button></div>
        <div class="dnote">${esc(t("stream_how"))}</div>
        ${st.port && st.wanted && st.port !== st.wanted ? `<div class="note warn">${ICON.info}<span>${esc(t("stream_port_moved", { port: st.wanted, used: st.port }))}</span></div>` : ""}
        <div class="note">${ICON.info}<span>${esc(st.viewers ? t("stream_viewers", { n: st.viewers }) : t("stream_waiting"))}</span></div>
        ${switchRow("stream_original", t("stream_original"))}
        ${switchRow("stream_mine", t("stream_mine"))}`;
    } else if (on && st.error) {
      body = `<div class="note warn">${ICON.info}<span>${esc(t("stream_port_busy", { port: st.wanted }))}</span></div>`;
    }
    return `<section id="stream-section"><div><h2>${esc(t("stream_title"))}</h2><div class="sub">${esc(t("stream_hint"))}</div></div>
        ${switchRow("stream", t("stream_on"))}${body}</section>`;
  }

  let previewTurn = 0;
  async function refreshPreview() {      // 浮動留言的預覽圖：照現在的設定重畫一張，載好了才換上去，不會閃
    const turn = ++previewTurn;
    try {
      const r = await fetch("/api/overlay_preview", { headers: { "X-LiveLingo-Token": TOKEN } });
      if (!r.ok || turn !== previewTurn) return;
      const url = URL.createObjectURL(await r.blob());
      if (S.ui.preview) URL.revokeObjectURL(S.ui.preview);
      S.ui.preview = url;
      const img = $("ov-preview");
      if (img) img.src = url;
    } catch (e) { /* 預覽只是輔助，載不到就維持上一張 */ }
  }

  function renderOverlayState() {
    const btn = $("btn-overlay");
    if (btn && S.cfg) btn.setAttribute("aria-pressed", S.cfg.overlay ? "true" : "false");
    const banner = $("ov-banner");
    if (!banner) return;
    banner.hidden = !S.overlay.editing;
    if (S.overlay.editing) banner.innerHTML = `<span>${esc(t("ov_editing"))}</span><button class="primary" data-act="overlay-edit">${esc(t("ov_done"))}</button>`;
  }

  function pairSelectHTML() {
    const options = (current) => PAIR_LANGS.map((c) =>
      `<option value="${c}" ${c === current ? "selected" : ""}>${esc(langName(c))}</option>`).join("");
    return `<div class="pair-select">
      <div class="field"><label for="sel-their">${esc(t("their_lang"))}</label><select id="sel-their" data-change="their_lang">
        <option value="auto" ${S.cfg.their_auto ? "selected" : ""}>${esc(S.cfg.their_auto ? t("auto_pair", { lang: langName(S.cfg.their_lang) }) : t("auto_detect"))}</option>${options(S.cfg.their_auto ? "" : S.cfg.their_lang)}</select></div>
      <button class="swap" data-act="swap" aria-label="${esc(t("swap"))}" title="${esc(t("swap"))}">${ICON.swap}</button>
      <div class="field"><label for="sel-my">${esc(t("my_lang"))}</label><select id="sel-my" data-change="my_lang">${options(S.cfg.my_lang)}</select></div>
    </div>`;
  }

  function uiLangsHTML() {
    const natives = [["zh-TW", "繁體中文"], ["zh-CN", "简体中文"], ["en", "English"], ["ko", "한국어"], ["ja", "日本語"], ["vi", "Tiếng Việt"], ["th", "ไทย"], ["id", "Bahasa Indonesia"], ["es", "Español"], ["pt", "Português"], ["ru", "Русский"], ["de", "Deutsch"], ["fr", "Français"]];
    return `<div class="pills ui-langs">${natives.map(([id, native]) => {      // 一排小按鈕：語言再多也只是多一顆，不會把版面擠開
      const on = S.cfg.ui_lang === id;
      return `<button class="pill tall" data-act="ui-lang" data-code="${id}" aria-pressed="${on}" title="${esc(on ? t("using") : t("click_switch"))}">${native}</button>`;
    }).join("")}</div>`;
  }

  // 健康檢查：每一項一個燈，旁邊可以直接去那一頁處理
  function healthText(it) {
    const p = Object.assign({}, it);
    if (it.quality) p.quality = t("h_q_" + it.quality);
    if (it.engine) p.engine = t("h_e_" + it.engine);
    return t("h_" + it.code, p);
  }
  function healthPage() {
    const h = S.ui.health;
    if (!h) return `<section><div><h2>${esc(t("h_title"))}</h2><div class="sub">${esc(t("h_hint"))}</div></div><div class="hist-none">…</div></section>`;
    const bad = h.items.filter((i) => i.state === "bad").length, warn = h.items.filter((i) => i.state === "warn").length;
    const summary = bad ? t("h_some_bad", { n: bad }) : warn ? t("h_some_warn", { n: warn }) : t("h_all_ok");
    const rows = h.items.map((it) => `<div class="setting-row health-row" data-state="${it.state}" id="health-${it.id}">
        <span class="lamp" role="img" aria-label="${esc(t("h_s_" + it.state))}"></span>
        <div class="health-text"><div>${esc(t("h_n_" + it.id))} <span class="health-state">${esc(t("h_s_" + it.state))}</span></div><div class="desc">${esc(healthText(it))}</div></div>
        ${it.state === "ok" ? "" : `<button class="ghost" data-act="nav" data-to="${it.tab}">${esc(t("h_go"))}</button>`}</div>`).join("");
    return `
      <section><div><h2>${esc(t("h_title"))}</h2><div class="sub">${esc(t("h_hint"))}</div></div>
        <div class="note ${bad ? "error" : warn ? "warn" : ""}" id="health-summary" aria-live="polite">${ICON.shield}<span>${esc(summary)}<br><small>${esc(t("h_checked", { time: new Date(h.at * 1000).toLocaleTimeString(S.cfg.ui_lang) }))}</small></span>
          <button class="ghost" data-act="health-again">${esc(t("h_again"))}</button></div>
        ${rows}
      </section>
      ${usageHTML()}
      ${reportHTML()}`;
  }
  // 說明：內容從後端拿（data/help.json，五種語言同一個結構），[[鍵]] 畫成按鍵的樣子；最下面可以存一頁給朋友的快速上手
  const KEY_MARK = /\[\[(.+?)\]\]/g;      // 先跳脫，再把 [[鍵]] 換成按鍵的樣子
  function helpPage() {
    const h = S.ui.help;
    if (!h) return `<section><div><h2>${esc(t("nav_help"))}</h2></div><div class="hist-none">…</div></section>`;
    const lang = S.ui.friendLang || h.friend_lang;
    const sections = h.sections.map((sec) => `<section class="help-sec" id="help-${sec.id}"><div><h2>${esc(sec.title)}</h2></div>
      <${sec.id === "start" ? "ol" : "ul"} class="help-list">${sec.items.map((i) => `<li>${esc(i).replace(KEY_MARK, "<kbd>$1</kbd>")}</li>`).join("")}</${sec.id === "start" ? "ol" : "ul"}></section>`).join("");
    const saved = S.ui.friendSaved;
    return `<section><div><h2>${esc(h.title)}</h2><div class="sub">${esc(h.intro)}</div></div></section>
      ${sections}
      <section id="help-friend"><div><h2>${esc(t("help_friend_title"))}</h2><div class="sub">${esc(t("help_friend_hint"))}</div></div>
        <div class="setting-row choice"><div>${esc(t("help_friend_lang"))}</div>
          <div class="pills" role="group" aria-label="${esc(t("help_friend_lang"))}">${h.langs.map((code) => `<button class="pill" data-act="friend-lang" data-code="${code}" aria-pressed="${code === lang}">${esc(langName(code))}</button>`).join("")}</div></div>
        <div class="btn-row"><button class="primary" data-act="friend-save">${esc(t("help_friend_save"))}</button></div>
        ${saved ? `<div class="note" id="help-saved" role="status">${ICON.info}<span>${esc(t("help_friend_saved", { name: saved.name }))}<br><small class="path">${esc(saved.folder)}</small></span></div>` : ""}</section>`;
  }
  async function loadHelp() {
    const r = await get("/api/help");
    if (!r || r.error) return;
    S.ui.help = r;
    if (S.view === "settings" && S.ui.tab === "help") renderSettingsPage();
  }
  // 使用數字：今天、最近 7 天、30 天各幾句、失敗幾次、平均等多久；下面是最近 14 天每天幾句的小圖
  function usageHTML() {
    const u = S.ui.usage;
    const head = `<div><h2>${esc(t("use_title"))}</h2><div class="sub">${esc(t("use_hint"))}</div></div>`;
    if (!u) return `<section id="usage">${head}<div class="hist-none">…</div></section>`;
    const num = (n) => Number(n).toLocaleString(S.cfg.ui_lang);
    const tile = (key, label) => {
      const d = u[key];
      return `<div class="stat" id="use-${key}"><div class="stat-label">${esc(label)}</div>
        <div class="stat-value">${esc(num(d.count))} <small>${esc(t("use_unit"))}</small></div>
        <div class="stat-line" data-bad="${d.failed > 0}">${esc(d.failed ? t("use_failed", { n: num(d.failed) }) : t("use_no_failed"))}</div>
        <div class="stat-line">${esc(d.wait == null ? t("use_no_wait") : t("use_wait", { s: d.wait.toFixed(1) }))}</div></div>`;
    };
    const max = Math.max(1, ...u.days.map((d) => d.count)), any = u.days.some((d) => d.count), short = (day) => day.slice(5).replace("-", "/");
    const bars = u.days.map((d, i) => {
      const tip = t("use_day", { day: short(d.day), n: num(d.count), failed: num(d.failed) });
      return `<li class="use-day" ${i === u.days.length - 1 ? 'data-today="true"' : ""} title="${esc(tip)}" aria-label="${esc(tip)}">
        ${i === u.days.length - 1 && d.count ? `<span class="use-top">${esc(num(d.count))}</span>` : ""}<span class="use-bar" style="height:${d.count ? Math.max(4, Math.round(100 * d.count / max)) : 0}%"></span></li>`;
    }).join("");
    const m = u.month;
    const chart = any ? `<figure class="use-chart"><ol class="use-bars">${bars}</ol>
        <div class="use-axis" aria-hidden="true"><span>${esc(short(u.days[0].day))}</span><span>${esc(t("use_today"))}</span></div>
        <figcaption>${esc(t("use_chart", { max: num(max) }))}</figcaption></figure>
      <div class="sub" id="use-split">${esc(t("use_month"))}：${esc(t("use_split", { heard: num(m.heard), screen: num(m.screen), spoken: num(m.spoken), typed: num(m.typed) }))}</div>` : "";
    const ask = S.ui.useAsk ? `<div class="note warn" id="use-ask">${ICON.info}<span>${esc(t("use_clear_ask"))}</span>
        <button class="danger" data-act="use-clear-yes">${esc(t("use_clear_yes"))}</button><button class="ghost" data-act="use-clear-no">${esc(t("cancel"))}</button></div>`
      : `<div class="btn-row"><button class="ghost" data-act="use-clear" ${u.since ? "" : "disabled"}>${esc(t("use_clear"))}</button>
        ${u.since ? `<span class="sub">${esc(t("use_since", { day: u.since }))}</span>` : ""}</div>`;
    return `<section id="usage">${head}
      ${u.since ? `<div class="stat-row">${tile("today", t("use_today"))}${tile("week", t("use_week"))}${tile("month", t("use_month"))}</div>${chart}`
        : `<div class="hist-none" id="use-empty">${esc(t("use_empty"))}</div>`}
      ${switchRow("usage_keep", t("use_keep"), t("use_keep_desc"))}
      ${ask}</section>`;
  }
  async function loadUsage() {
    const r = await get("/api/usage");
    if (!r || r.error) return;
    S.ui.usage = r;
    if (S.view === "settings" && S.ui.tab === "health") renderSettingsPage();
  }
  // 問題報告：先做出來整份給使用者看，他按了才存成檔案或複製；程式不會自己送出任何東西
  function reportHTML() {
    const r = S.ui.report, parts = S.ui.reportParts || (S.ui.reportParts = { settings: true, log: true });
    const head = `<div><h2>${esc(t("rep_title"))}</h2><div class="sub">${esc(t("rep_hint"))}</div></div>`;
    const part = (key) => `<div class="setting-row"><div><div>${esc(t("rep_part_" + key))}</div><div class="desc">${esc(t("rep_part_" + key + "_desc"))}</div></div>
      <button class="switch" role="switch" data-act="report-part" data-key="${key}" aria-checked="${parts[key] ? "true" : "false"}" aria-label="${esc(t("rep_part_" + key))}"><span></span></button></div>`;
    if (!r) {
      return `<section id="report">${head}
        <label class="rep-label" for="rep-note">${esc(t("rep_note"))}</label>
        <textarea id="rep-note" rows="3" maxlength="2000" placeholder="${esc(t("rep_note_hint"))}"></textarea>
        ${part("settings")}${part("log")}
        <div class="btn-row"><button class="primary" data-act="report-make">${esc(t("rep_make"))}</button></div></section>`;
    }
    return `<section id="report">${head}
      <div class="note" id="rep-unsent">${ICON.shield}<span>${esc(t("rep_unsent", { lines: r.lines, size: sizeText(r.bytes) }))}</span></div>
      <pre id="rep-text" tabindex="0" aria-label="${esc(t("rep_title"))}">${esc(r.text)}</pre>
      <div class="btn-row" id="rep-buttons"><button class="primary" data-act="report-save">${esc(t("rep_save"))}</button>
        <button class="ghost" data-act="report-copy">${esc(t("rep_copy"))}</button>
        <button class="ghost" data-act="report-back">${esc(t("rep_back"))}</button></div>
      ${r.saved ? `<div class="note" id="rep-saved" role="status">${ICON.info}<span>${esc(t("rep_saved", { name: r.saved }))}<br><small class="path">${esc(r.folder)}</small></span></div>` : ""}</section>`;
  }
  // 儲存空間：大檔案放在哪、各佔多少、搬到別的磁碟、刪掉沒在用的
  function storagePage() {
    const st = S.ui.storage;
    const head = `<div><h2>${esc(t("sto_title"))}</h2><div class="sub">${esc(t("sto_hint"))}</div></div>`;
    if (!st) return `<section>${head}<div class="hist-none">…</div></section>`;
    const m = st.moving;
    const moving = m ? `<div class="note" id="store-moving">${ICON.disk}<span class="store-progress"><span id="store-moving-text">${esc(t("sto_moving", { folder: m.to, done: sizeText(m.done), total: m.total ? sizeText(m.total) : "…" }))}</span>
        <div class="bar"><span id="store-moving-bar" style="width:${m.total ? Math.round(100 * m.done / m.total) : 0}%"></span></div></span>
        <button class="ghost" data-act="store-cancel">${esc(t("cancel"))}</button></div>` : "";
    const lost = st.lost ? `<div class="note warn" id="store-lost">${ICON.info}<span>${esc(t("sto_lost", { folder: st.lost }))}</span></div>` : "";
    const pick = st.can_pick ? `<button class="ghost" data-act="store-choose" ${m ? "disabled" : ""}>${esc(t("sto_move"))}</button>` : "";
    const typed = st.can_pick ? "" : `<form class="btn-row" id="store-form"><input type="text" id="store-path" name="path" maxlength="240" placeholder="${esc(t("sto_path"))}" aria-label="${esc(t("sto_path"))}" ${m ? "disabled" : ""}>
        <button class="ghost" type="submit" ${m ? "disabled" : ""}>${esc(t("sto_path_go"))}</button></form>`;
    const rows = st.items.map((it) => {
      const name = t("sto_i_" + it.id);
      if (S.ui.storeAsk === it.id && it.removable) {
        return `<div class="note warn" id="store-ask">${ICON.info}<span>${esc(t("sto_remove_ask", { name, size: sizeText(it.bytes) }))}</span>
          <button class="danger" data-act="store-remove-yes" data-id="${it.id}">${esc(t("sto_remove"))}</button><button class="ghost" data-act="store-remove-no" data-id="${it.id}">${esc(t("cancel"))}</button></div>`;
      }
      return `<div class="setting-row store-row" id="store-${it.id}">
        <div class="health-text"><div>${esc(name)}${it.id === "records" ? "" : ` <span class="store-state" data-used="${it.used}">${esc(t(it.used ? "sto_used" : "sto_unused"))}</span>`}</div>
          <div class="desc">${esc(t("sto_d_" + it.id, { folder: st.default }))}</div></div>
        <span class="store-size">${esc(sizeText(it.bytes))}</span>
        ${it.removable ? `<button class="ghost" data-act="store-remove" data-id="${it.id}" ${m ? "disabled" : ""}>${esc(t("sto_remove"))}</button>` : ""}</div>`;
    }).join("");
    return `
      <section>${head}${lost}
        <div class="setting-row"><div class="health-text"><div>${esc(t("sto_where"))}</div><div class="desc path" id="store-folder">${esc(st.folder)}</div>
          <div class="desc" id="store-total">${esc(t("sto_total", { size: sizeText(st.total) }))} · ${esc(t("sto_free", { size: sizeText(st.free) }))}</div></div></div>
        ${moving}
        <div class="btn-row" id="store-buttons"><button class="ghost" data-act="store-open">${esc(t("sto_open"))}</button>${pick}
          ${st.moved ? `<button class="ghost" data-act="store-back" ${m ? "disabled" : ""}>${esc(t("sto_back"))}</button>` : ""}</div>
        ${typed}
        <div class="sub">${esc(t("sto_move_hint"))}</div>
      </section>
      <section><div><h2>${esc(t("sto_items"))}</h2></div>${rows}</section>`;
  }
  async function loadStorage() {
    const r = await get("/api/storage");
    if (!r || r.error) return;
    S.ui.storage = r;
    if (S.view === "settings" && S.ui.tab === "storage") renderSettingsPage();
  }
  function storeAnswer(r) {      // 搬、刪的要求被擋下來：說原因
    if (r && !r.ok && r.error) toast(t("n_" + r.error, r), 8000, "warn");
  }

  function settingsApplied(r, key) {      // 匯入、恢復預設、復原之後：說結果、記下還能不能復原、把畫面換成新的設定
    if (!r || !r.ok) { toast(t(r && r.error === "not_settings" ? "set_bad" : "n_save_failed", { error: "" }), 8000, "warn"); return; }
    S.settingsBackup = r.backup || null;
    if (!r.saved) toast(t("n_save_failed", { error: "" }), 8000, "error");
    else { toast(t(key, { n: r.count }), 7000); flashSaved("#set-buttons"); }
    renderSettingsPage();
  }
  let healthTimer = null;
  async function loadHealth() {
    const goReport = S.ui.goReport;      // 從出錯畫面按「回報問題」來的：頁面畫好之後直接捲到回報那一塊
    S.ui.goReport = false;
    const r = await get("/api/health");
    if (!r || r.error) return;
    S.ui.health = r;
    if (S.view === "settings" && S.ui.tab === "health") {
      renderSettingsPage();
      if (goReport && $("report")) $("report").scrollIntoView({ block: "start" });
    }
  }
  function healthSoon() {      // 狀態變了（引擎好了、來源開關了）：正在看這一頁就等一下重查
    if (S.view !== "settings" || S.ui.tab !== "health") return;
    clearTimeout(healthTimer);
    healthTimer = setTimeout(loadHealth, 700);
  }

  // 對話歷史：存在這台電腦上的句子。搜尋和「載入更多」只換清單那一塊，不重畫整頁（正在打的字不會被打斷）
  function sizeText(bytes) {
    const b = Number(bytes) || 0;
    if (b >= 1073741824) return (b / 1073741824).toFixed(1) + " GB";
    return b >= 1048576 ? (b / 1048576).toFixed(b >= 104857600 ? 0 : 1) + " MB" : Math.max(b ? 1 : 0, Math.round(b / 1024)) + " KB";
  }
  function histListHTML() {
    const h = S.ui.hist;
    if (!h) return "";
    if (!h.items.length) return `<div class="hist-none">${esc(t(h.q ? "hist_none" : S.cfg.history_keep ? "hist_empty" : "hist_off_empty"))}</div>`;
    let day = "";
    const rows = h.items.map((m) => {
      const at = new Date(m.at * 1000), d = at.toLocaleDateString(S.cfg.ui_lang, { year: "numeric", month: "long", day: "numeric", weekday: "short" });
      const head = d !== day ? `<div class="hist-day">${esc(d)}</div>` : "";
      day = d;
      const same = !m.translated || m.translated === m.original;
      return `${head}<div class="hist-row ${m.who === "me" ? "me" : "them"}">
        <div class="hist-meta">${esc(at.toLocaleTimeString(S.cfg.ui_lang, { hour: "2-digit", minute: "2-digit" }))} · ${esc(t("who_" + m.who))} · ${esc(t("via_" + m.via))}${m.place ? " · " + esc(m.place) : ""}</div>
        <div class="hist-original">${esc(m.original)}</div>${same ? "" : `<div class="hist-translated">${esc(m.translated)}</div>`}</div>`;
    }).join("");
    return rows + (h.more ? `<div class="btn-row"><button class="ghost" data-act="hist-more">${esc(t("hist_more"))}</button></div>` : "");
  }
  function histToolsHTML() {
    const h = S.ui.hist || { q: "", total: S.history.count, all: S.history.count };
    const count = h.q ? t("hist_found", { n: h.total, all: h.all }) : t("hist_count", { n: S.history.count, size: sizeText(S.history.bytes) });
    if (S.ui.histConfirm) {
      return `<div class="note warn" id="hist-confirm">${ICON.info}<span>${esc(t("hist_clear_ask", { n: S.history.count }))}</span>
        <button class="danger" data-act="hist-clear-yes">${esc(t("hist_clear_yes"))}</button><button class="ghost" data-act="hist-clear-no">${esc(t("cancel"))}</button></div>`;
    }
    return `<div class="hist-tools"><span class="hist-count" id="hist-count">${esc(count)}</span>
      <button class="ghost" data-act="hist-export" ${h.total ? "" : "disabled"}>${esc(h.q ? t("hist_export_found", { n: h.total }) : t("hist_export"))}</button>
      <button class="ghost" data-act="hist-clear" ${S.history.count ? "" : "disabled"}>${esc(t("hist_clear"))}</button></div>`;
  }
  function historyPage() {
    return `
      <section><div><h2>${esc(t("hist_title"))}</h2><div class="sub">${esc(t("hist_hint"))}</div></div>
        ${switchRow("history_keep", t("hist_keep"), t("hist_keep_desc"))}
        ${S.cfg.history_keep ? choiceRow("history_days", t("hist_days"), [["7", t("hist_d7")], ["30", t("hist_d30")], ["90", t("hist_d90")], ["0", t("hist_d0")]]) : ""}
      </section>
      <section id="recap-section"><div><h2>${esc(t("recap_title"))}</h2><div class="sub">${esc(t("recap_hint"))}</div></div>
        ${switchRow("recap_auto", t("recap_auto"), t("recap_auto_desc"))}
        <div id="recap-list">${recapListHTML()}</div>
      </section>
      <section>
        <input type="text" id="hist-q" class="hist-q" maxlength="80" placeholder="${esc(t("hist_search"))}" aria-label="${esc(t("hist_search"))}" value="${esc((S.ui.hist && S.ui.hist.q) || "")}">
        <div id="hist-tools">${histToolsHTML()}</div>
        <div class="hist-list" id="hist-list" aria-live="polite">${histListHTML()}</div>
      </section>`;
  }
  function paintHistory() {      // 只換工具列和清單
    const tools = $("hist-tools"), list = $("hist-list"), recaps = $("recap-list");
    if (tools) tools.innerHTML = histToolsHTML();
    if (list) list.innerHTML = histListHTML();
    if (recaps) recaps.innerHTML = recapListHTML();
  }

  // 對話摘要：一段對話一張卡（什麼時候、幾句、幾個重點）；跟著上面的搜尋一起找
  function recapListHTML() {
    const thinking = S.recaps.state === "thinking";
    const tools = `<div class="btn-row"><button class="ghost" data-act="recap-now" ${thinking ? "disabled" : ""}>${esc(t(thinking ? "recap_thinking" : "recap_now"))}</button></div>`;
    const list = S.ui.recapList;
    if (!list) return tools;
    const q = S.ui.hist && S.ui.hist.q;
    if (!list.length) return tools + `<div class="hist-none">${esc(t(q ? "recap_none_found" : "recap_empty"))}</div>`;
    const when = (r) => {
      const a = new Date(r.start * 1000), b = new Date(r.end * 1000);
      const time = (d) => d.toLocaleTimeString(S.cfg.ui_lang, { hour: "2-digit", minute: "2-digit" });
      return a.toLocaleDateString(S.cfg.ui_lang, { month: "long", day: "numeric", weekday: "short" }) + " " + time(a) + "–" + time(b);
    };
    return tools + list.map((r) => `<div class="recap"><div class="recap-head"><span>${esc(when(r))} · ${esc(t("recap_lines", { n: r.lines }))}</span>
      <button class="icon-btn" data-act="recap-remove" data-id="${esc(r.id)}" aria-label="${esc(t("recap_remove"))}" title="${esc(t("recap_remove"))}">${ICON.x}</button></div>
      <ul>${r.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`).join("");
  }
  let recapSeq = 0;
  async function loadRecaps() {
    const seq = ++recapSeq, q = S.ui.hist ? S.ui.hist.q : "";
    const r = await get(`/api/recaps?q=${encodeURIComponent(q || "")}`);
    if (!r || r.error || seq !== recapSeq) return;
    S.ui.recapList = r.items;
    const box = $("recap-list");
    if (box && S.view === "settings" && S.ui.tab === "history") box.innerHTML = recapListHTML();
  }

  // 學習頁：學習模式的開關、今天要複習的、單字本（可以搜尋、刪掉）。搜尋只換清單那一塊
  function learnPage() {
    const wb = S.wordbook || { count: 0, due: 0 };
    return `
      <section><div><h2>${esc(t("learn_title"))}</h2><div class="sub">${esc(t("learn_hint"))}</div></div>
        ${switchRow("learn_mode", t("learn_mode"), t("learn_mode_desc"))}
      </section>
      <section id="review-section"><div><h2>${esc(t("learn_review"))}</h2></div>${reviewHTML(wb)}</section>
      <section><div><h2>${esc(t("learn_book"))}</h2><div class="sub">${esc(t("learn_book_hint"))}</div></div>
        <input type="text" id="word-q" class="hist-q" maxlength="40" placeholder="${esc(t("learn_search"))}" aria-label="${esc(t("learn_search"))}" value="${esc((S.ui.words && S.ui.words.q) || "")}">
        <div class="hist-list" id="word-list" aria-live="polite">${wordListHTML()}</div>
      </section>`;
  }
  function reviewHTML(wb) {
    const rv = S.ui.review;
    if (!rv) {
      return `<div class="setting-row"><div>${esc(wb.due ? t("learn_due", { n: wb.due }) : t(wb.count ? "learn_due_none" : "learn_empty"))}</div>
        <button class="primary" data-act="review-start" ${wb.due ? "" : "disabled"}>${esc(t("learn_review_start"))}</button></div>`;
    }
    const w = rv.items[rv.i];
    if (!w) return `<div class="note">${ICON.check}<span>${esc(t("learn_review_done", { n: rv.items.length }))}</span></div>`;
    return `<div class="card-word"><div class="card-count">${esc(t("learn_left", { n: rv.items.length - rv.i }))} · ${esc(langName(w.lang === "zh" ? "zh-TW" : w.lang))}</div>
      <div class="card-w">${esc(w.w)}</div>
      ${rv.show ? `<div class="card-m">${esc(w.meaning || t("learn_no_meaning"))}</div>${w.example ? `<div class="card-ex">${esc(w.example)}${w.example_tr ? `<br><span>${esc(w.example_tr)}</span>` : ""}</div>` : ""}
        <div class="btn-row"><button class="primary" data-act="review-answer" data-value="1">${esc(t("learn_remember"))}</button><button class="ghost" data-act="review-answer" data-value="">${esc(t("learn_forgot"))}</button></div>`
        : `<div class="btn-row"><button class="primary" data-act="review-show">${esc(t("learn_show"))}</button></div>`}</div>`;
  }
  function wordListHTML() {
    const ws = S.ui.words;
    if (!ws) return "";
    if (!ws.items.length) return `<div class="hist-none">${esc(t(ws.q ? "learn_none_found" : "learn_empty"))}</div>`;
    return ws.items.map((w) => `<div class="hist-row word-row"><div><span class="word-w">${esc(w.w)}</span> <span class="word-m">${esc(w.meaning || t("learn_looking"))}</span>
      ${w.example ? `<div class="hist-meta">${esc(w.example)}</div>` : ""}</div>
      <button class="icon-btn" data-act="word-remove" data-w="${esc(w.w)}" data-lang="${esc(w.lang)}" aria-label="${esc(t("learn_remove", { w: w.w }))}" title="${esc(t("learn_remove", { w: w.w }))}">${ICON.x}</button></div>`).join("");
  }
  let wordSeq = 0;
  async function loadWords() {
    const seq = ++wordSeq, q = S.ui.words ? S.ui.words.q : "";
    const r = await get(`/api/words?q=${encodeURIComponent(q)}`);
    if (!r || r.error || seq !== wordSeq) return;
    S.wordbook = { count: r.count, due: r.due };
    S.ui.words = { q, items: r.items.slice(0, 300) };
    if (S.view !== "settings" || S.ui.tab !== "learn") return;
    const list = $("word-list"), rv = $("review-section");
    if (list) list.innerHTML = wordListHTML();
    if (rv && !S.ui.review) rv.innerHTML = `<div><h2>${esc(t("learn_review"))}</h2></div>${reviewHTML(S.wordbook)}`;
  }
  function paintReview() {
    const rv = $("review-section");
    if (rv) rv.innerHTML = `<div><h2>${esc(t("learn_review"))}</h2></div>${reviewHTML(S.wordbook)}`;
  }
  let wordTimer = null;

  let histSeq = 0;
  async function loadHistory(more) {
    const q = S.ui.hist ? S.ui.hist.q : "", seq = ++histSeq;
    const offset = more && S.ui.hist ? S.ui.hist.items.length : 0;
    const r = await get(`/api/history?q=${encodeURIComponent(q)}&offset=${offset}&limit=50`);
    if (!r || r.error || seq !== histSeq) return;      // 打字打得快：只留最後一次查的結果
    S.history = { count: r.count, bytes: r.bytes };
    S.ui.hist = { q, items: more ? S.ui.hist.items.concat(r.items) : r.items, total: r.total, all: r.all, more: r.more };
    if (S.view === "settings" && S.ui.tab === "history") paintHistory();
  }
  let histTimer = null;

  // 全域快速鍵：按一下那顆鍵帽就開始等新的組合；等的時候先請背景把登記的放掉，免得按到現在那一組直接觸發
  const HOTKEYS = ["overlay", "mic", "copy", "quick", "reply", "pick", "snap", "phrase"];
  const comboHTML = (combo) => combo.split("+").map((k) => `<kbd>${esc(k)}</kbd>`).join("");
  function hotkeysHTML() {
    const hk = S.hotkeys || { available: false, states: {} };
    const row = (name) => {
      const combo = (S.cfg.hotkeys || {})[name] || "", rec = S.ui.hotkeyRec === name, state = (hk.states || {})[name] || "off";
      const status = rec ? `<div class="hk-state">${esc(t("hk_rec_hint"))}</div>`
        : !hk.available || !combo ? ""
        : state === "taken" ? `<div class="hk-state taken">${esc(t("hk_taken"))}</div>`
        : state === "ok" ? `<div class="hk-state ok">${ICON.check}<span>${esc(t("hk_ok"))}</span></div>` : "";
      return `<div class="setting-row hotkey-row"><div><div>${esc(t("hk_" + name))}</div><div class="desc">${esc(t("hk_" + name + "_desc"))}</div>${status}</div>
        <div class="hk-keys">
          <button class="keycap${rec ? " rec" : ""}${combo ? "" : " empty"}" id="hk-${name}" data-act="hotkey-set" data-name="${name}"
            aria-label="${esc(t("hk_change", { name: t("hk_" + name), combo: combo || t("hk_none") }))}">${rec ? esc(t("hk_press")) : combo ? comboHTML(combo) : esc(t("hk_none"))}</button>
          ${combo && !rec ? `<button class="icon-btn" data-act="hotkey-clear" data-name="${name}" aria-label="${esc(t("hk_clear", { name: t("hk_" + name) }))}">${ICON.x}</button>` : `<span class="icon-btn" aria-hidden="true"></span>`}
        </div></div>`;
    };
    return (hk.available ? "" : `<div class="note">${ICON.info}<span>${esc(t("hk_unavailable"))}</span></div>`)
      + HOTKEYS.map(row).join("")
      + switchRow("pick_copy", t("pick_copy"), t("pick_copy_desc"))
      + `<div class="btn-row"><button class="ghost" data-act="quick-try">${esc(t("hk_try"))}</button>
          <button class="ghost" data-act="hotkey-reset">${esc(t("hk_reset"))}</button></div>`;
  }

  // 常用句庫：事先存好常說的話，每一句已經翻成對方的語言（改了對方的語言會在背景補翻）
  function phrasebookHTML() {
    const ph = S.phrases || { items: [], max: 36 };
    const rows = ph.items.map((it) => `<div class="setting-row phrase-row"><div class="phrase-text"><div>${esc(it.text)}</div>
        <input type="text" class="phrase-tr" data-phrase-edit="${esc(it.id)}" maxlength="120" value="${esc(it.tr)}" placeholder="${esc(t("ph_translating"))}" aria-label="${esc(t("ph_edit"))}"></div>
        <button class="icon-btn" data-act="phrase-remove" data-id="${esc(it.id)}" aria-label="${esc(t("ph_remove", { text: it.text }))}" title="${esc(t("ph_remove", { text: it.text }))}">${ICON.x}</button></div>`).join("");
    return `<div class="phrase-add"><input type="text" id="phrase-new" maxlength="120" placeholder="${esc(t("ph_placeholder"))}" aria-label="${esc(t("ph_placeholder"))}">
        <button class="primary" data-act="phrase-add">${esc(t("ph_add"))}</button></div>
      ${rows || `<div class="hist-none">${esc(t("ph_empty"))}</div>`}
      ${ph.items.length ? "" : `<div class="btn-row"><button class="ghost" data-act="phrase-starters">${esc(t("ph_starters"))}</button></div>`}`;
  }
  function alertHTML() {
    return `<div class="setting-row choice"><input type="text" id="alert-words" class="hist-q" maxlength="200" data-change="alert_words" value="${esc((S.cfg.alert_words || []).join(", "))}"
        placeholder="${esc(t("al_placeholder"))}" aria-label="${esc(t("al_placeholder"))}"></div>${switchRow("alert_sound", t("al_sound"))}`;
  }
  async function phraseDo(body) {
    const r = await post("/api/phrases", body);
    if (r && r.items) S.phrases = r;
    if (r && !r.ok && body.action === "add") toast(t("ph_not_added"), 6000, "warn");
    if (S.view === "settings" && S.ui.tab === "general") { const keep = document.activeElement && document.activeElement.id; renderSettingsPage(); if (keep) { const el = $(keep); if (el) el.focus(); } }
    return r;
  }
  function hotkeyRecord(name) {      // name：開始等哪個動作的新組合；null：不等了
    if ((S.ui.hotkeyRec || null) === (name || null)) return;
    S.ui.hotkeyRec = name || null;
    post("/api/hotkeys", { pause: !!name });
    if (S.view === "settings" && S.ui.tab === "general") renderSettingsPage();
  }
  async function saveHotkey(name, combo) {
    S.ui.hotkeyRec = null;
    await post("/api/hotkeys", { pause: false });
    const r = await post("/api/config", { hotkeys: { [name]: combo } });
    if (!(r && r.ok && r.saved)) toast(t("n_save_failed", { error: (r && r.error) || "" }), 8000, "error");
    else if ((r.changed || []).includes("hotkeys")) { $("toast").hidden = true; flashSaved("#hk-" + name); }      // 沒被收下（跟別的動作撞到）就不說已儲存
    if (S.view === "settings" && S.ui.tab === "general") renderSettingsPage();
  }

  function settingsFileHTML() {      // 設定的匯出、匯入、恢復預設；做完留一份可以復原
    const b = S.settingsBackup;
    const undo = b ? `<div class="note" id="set-undo">${ICON.info}<span>${esc(t(b.reason === "import" ? "set_undo_import" : "set_undo_reset"))}</span>
        <button data-act="set-undo">${esc(t("set_undo"))}</button><button class="ghost" data-act="set-forget">${esc(t("set_keep"))}</button></div>` : "";
    const ask = S.ui.resetAsk ? `<div class="note warn" id="set-ask">${ICON.info}<span>${esc(t("set_reset_ask"))}</span>
        <button class="danger" data-act="set-reset-yes">${esc(t("set_reset_yes"))}</button><button class="ghost" data-act="set-reset-no">${esc(t("cancel"))}</button></div>` : "";
    return `${undo}${ask}<div class="btn-row" id="set-buttons">
        <button class="ghost" data-act="set-export">${esc(t("set_export"))}</button>
        <button class="ghost" data-act="set-import">${esc(t("set_import"))}</button>
        <input type="file" id="set-file" accept=".json,application/json" hidden>
        ${S.ui.resetAsk ? "" : `<button class="ghost" data-act="set-reset">${esc(t("set_reset"))}</button>`}</div>`;
  }

  function generalPage() {
    return `
      <section><div><h2>${esc(t("ui_title"))}</h2><div class="sub">${esc(t("ui_hint"))}</div></div>${uiLangsHTML()}</section>
      <section><div><h2>${esc(t("trans_title"))}</h2><div class="sub">${esc(t("trans_hint"))}</div></div>${pairSelectHTML()}</section>
      <section><div><h2>${esc(t("theme_title"))}</h2><div class="sub">${esc(t("theme_hint"))}</div></div>${themeHTML()}</section>
      <section><div><h2>${esc(t("rec_title"))}</h2><div class="sub">${esc(t("rec_hint"))}</div></div>${recordHTML()}</section>
      <section><h2>${esc(t("window_title"))}</h2>
        ${S.canWindow ? switchRow("on_top", t("on_top")) : ""}
        ${S.canWindow ? switchRow("close_to_tray", t("close_to_tray"), t("close_to_tray_desc")) : ""}
        ${switchRow("show_pronunciation", t("show_pron"))}
        ${switchRow("copy_spoken", t("copy_spoken"))}
      </section>
      <section><div><h2>${esc(t("hk_title"))}</h2><div class="sub">${esc(t("hk_hint"))}</div></div>${hotkeysHTML()}</section>
      <section id="phrase-section"><div><h2>${esc(t("ph_title"))}</h2><div class="sub">${esc(t("ph_hint"))}</div></div>${phrasebookHTML()}</section>
      <section><div><h2>${esc(t("al_title"))}</h2><div class="sub">${esc(t("al_hint"))}</div></div>${alertHTML()}</section>
      <section><div><h2>${esc(t("set_title"))}</h2><div class="sub">${esc(t("set_hint"))}</div></div>${settingsFileHTML()}</section>
      <section><div class="btn-row"><button class="ghost" data-act="rerun-setup">${esc(t("rerun_setup"))}</button></div></section>`;
  }

  // 每一版改了什麼：用現在的介面語言，沒有那個語言就用英文
  function changesHTML(list, withVersion) {
    return list.map((v) => {
      const items = v.items[S.cfg.ui_lang] || v.items.en || v.items["zh-TW"] || [];
      return `${withVersion ? `<div class="changes-version">${esc(t("about_changes_version", { version: v.version }))}</div>` : ""}<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
    }).join("");
  }
  function aboutChangesHTML() {
    const all = Object.keys(S.changes || {}).map((version) => ({ version, items: S.changes[version] }));
    const now = all.filter((v) => v.version === S.version), older = all.filter((v) => v.version !== S.version);
    if (!now.length && !older.length) return "";
    return `<section id="about-changes"><div><h2>${esc(t("about_changes_title"))}</h2><div class="sub">${esc(t("about_version", { version: S.version }))}</div></div>
        ${now.length ? `<div class="changes">${changesHTML(now, false)}</div>` : ""}
        ${older.length ? `<details class="changes-older"><summary>${esc(t("about_changes_older"))}</summary><div class="changes">${changesHTML(older, true)}</div></details>` : ""}</section>`;
  }

  // 回上一版：更新之後覺得新版有問題，可以退回去；退回之後不再問要不要更新到那一版
  function rollbackHTML() {
    const u = S.update || {}, p = u.previous;
    let html = "";
    if (u.skipped) {
      html += `<div class="note" id="up-skipped">${ICON.info}<span>${esc(t("up_skipped", { version: u.skipped }))}</span>
        <button class="ghost" data-act="update-unskip">${esc(t("up_unskip"))}</button></div>`;
    }
    if (p && S.ui.backAsk) {
      html += `<div class="note warn" id="up-back-ask">${ICON.info}<span>${esc(t("up_back_ask", { version: p.version, current: S.version }))}</span>
        <button data-act="rollback-yes">${esc(t("up_back_yes"))}</button><button class="ghost" data-act="rollback-no">${esc(t("cancel"))}</button></div>`;
    } else if (p) {
      html += `<div class="setting-row store-row" id="up-back"><div class="health-text"><div>${esc(t("up_back_row", { version: p.version }))}</div>
        <div class="desc">${esc(t("up_back_fetch"))}</div></div>
        <button class="ghost" data-act="rollback">${esc(t("up_back", { version: p.version }))}</button></div>`;
    }
    return html ? `<section id="about-update">${html}</section>` : "";
  }

  function aboutPage() {
    const fact = (icon, title, text) => `<div class="fact">${ICON[icon]}<div><b>${esc(title)}</b><span>${esc(text)}</span></div></div>`;
    return `
      <section class="about-hero">${MARK}
        <div><div class="wordmark big">${WORDMARK}</div><div class="tagline">${esc(t("tagline"))}</div>
        <div class="version">${esc(t("about_version", { version: S.version }))}</div></div>
      </section>
      <section>
        ${fact("shield", t("about_local_title"), t("about_local"))}
        ${fact("quality", t("about_engine_title"), t("about_engine"))}
        ${fact("info", t("about_update_title"), t(S.cfg.update_check ? "about_update" : "about_update_off"))}
        ${switchRow("update_check", t("update_check"), t("update_check_desc"))}
      </section>
      ${rollbackHTML()}
      ${aboutChangesHTML()}`;
  }

  function renderSettingsPage() {
    const page = $("settings-page");
    const scroll = page.scrollTop;
    const active = page.contains(document.activeElement) ? document.activeElement : null;
    const focus = !active ? "" : active.id ? "#" + active.id : active.dataset.act
      ? `[data-act="${active.dataset.act}"]` + ["key", "value", "code", "q", "tone", "id", "pack", "exe", "work"].filter((k) => active.dataset[k] != null).map((k) => `[data-${k}="${CSS.escape(active.dataset[k])}"]`).join("") : "";
    const typed = [...page.querySelectorAll("input[id]:not([type=range]):not([type=file]), textarea[id]")].map((el) => [el.id, el.value]);
    if (S.ui.tab === "history") page.innerHTML = historyPage();
    else if (S.ui.tab === "learn") page.innerHTML = learnPage();
    else if (S.ui.tab === "health") page.innerHTML = healthPage();
    else if (S.ui.tab === "storage") page.innerHTML = storagePage();
    else if (S.ui.tab === "audio") page.innerHTML = devicesHTML() + speakHTML() + voiceTuningHTML();
    else if (S.ui.tab === "quality") page.innerHTML = qualityPage();
    else if (S.ui.tab === "screen") page.innerHTML = screenPage();
    else if (S.ui.tab === "overlay") page.innerHTML = overlayPage();
    else if (S.ui.tab === "about") page.innerHTML = aboutPage();
    else if (S.ui.tab === "help") page.innerHTML = helpPage();
    else page.innerHTML = generalPage();
    if (S.ui.tab !== "about" && S.ui.tab !== "help" && S.ui.tab !== "health" && S.ui.tab !== "storage") page.insertAdjacentHTML("beforeend", `<div class="autosave">${ICON.check}<span>${esc(t("autosave"))}</span></div>`);
    typed.forEach(([id, value]) => { const el = $(id); if (el && value && !el.value) el.value = value; });
    page.scrollTop = scroll;
    const again = focus && page.querySelector(focus);
    if (again && !again.disabled) again.focus({ preventScroll: true });
    paintLevels();
    placeSaved();
    if (S.ui.tab === "overlay" && !S.ui.preview) refreshPreview();
  }

  function renderSettings() { renderRail(); renderSettingsPage(); }

  function openTab(tab) {
    const wasAudio = S.ui.tab === "audio";
    S.ui.tab = tab;
    renderSettings();
    renderOverlay();      // 出錯畫面的蓋板在「回報問題」那一頁會收起來，離開那一頁要再蓋回去
    const page = $("settings-page");
    page.scrollTop = 0;
    page.classList.remove("in"); void page.offsetWidth; page.classList.add("in");      // 換頁的淡入
    tabOpened();
    if (tab === "overlay") refreshPreview();
    else if (wasAudio) post("/api/meters", { on: false });
  }

  // ---------------------------------------------------------------- 第一次設定

  function downloadLine() {
    const d = S.detail || {};
    return t("dl_progress", { name: t("dl_" + (d.key || "model")), done: d.done_mb || 0, total: d.total_mb || 0 });
  }

  function renderWizardStrip() {
    const el = $("wizard-dl");
    if (S.view !== "wizard" || S.ui.wz === 0) { el.hidden = true; return; }
    if (S.phase === "downloading") {
      el.hidden = false;
      el.innerHTML = `<div class="line"><b>${esc(t("dl_title"))}</b><span>${esc(downloadLine())}</span></div>
        <div class="bar"><span style="width:${percent()}%"></span></div>`;
    } else if (S.phase === "need_download") {
      el.hidden = false;
      el.innerHTML = `<div class="line"><b>${esc(S.detail.error ? t("dl_error", { error: S.detail.error }) : t("dl_need", { gb: gb(S.detail.total_mb) }))}</b>
        <button class="link" data-act="download">${esc(S.detail.error ? t("retry") : t("dl_start"))}</button></div>`;
    } else if (S.phase === "loading" || S.phase === "starting") {
      el.hidden = false;
      el.innerHTML = `<div class="line"><b>${esc(t("st_" + S.phase))}</b></div>`;
    } else {
      el.hidden = true;
    }
  }

  function stepsHTML() {
    const items = [["wz_s1", ""], ["wz_s2", ""], ["wz_s3", "wz_s3_sub"]];
    return `<ol class="steps">${items.map(([key, sub], i) => {
      const n = i + 1;
      const cls = n < S.ui.wz ? "done" : (n === S.ui.wz ? "now" : "");
      const subText = n < S.ui.wz ? t("wz_done") : (n === S.ui.wz ? t("wz_now") : (sub ? t(sub) : ""));
      return `<li class="${cls}"><span class="num">${n < S.ui.wz ? ICON.check : n}</span><span class="lbl"><b>${esc(t(key))}</b><span>${esc(subText)}</span></span></li>`;
    }).join("")}</ol>`;
  }

  function wizardBody() {
    const names = shortNames();
    if (S.ui.wz === 1) return `<div class="wz-body">${devicesHTML()}</div>`;
    if (S.ui.wz === 2) {
      const r = regions()[0];
      return `<div class="wz-body"><div class="wz-split">
        <div class="wz-art" aria-hidden="true"><div class="t"></div>
          <div class="box"><span class="tag">${esc(t("wz_region_here"))}</span>
            <div class="ln"><i></i><b style="width:150px"></b></div><div class="ln"><i></i><b style="width:200px"></b></div><div class="ln"><i></i><b style="width:120px"></b></div></div>
          <div class="inp">${esc(t("wz_region_not"))}</div></div>
        <div class="wz-side">
          <h2>${esc(t("wz_region_title"))}</h2>
          <div class="lead">${esc(t("wz_region_body"))}</div>
          ${r ? `<div class="note">${ICON.info}<span>${esc(t("wz_region_ok", { w: r.width, h: r.height }))}</span></div>` : ""}
          <button class="primary big" data-act="wz-region">${ICON.frame}${esc(r ? t("wz_region_again") : t("wz_region_go"))}</button>
          ${r ? "" : `<button class="link" data-act="wz-next">${esc(t("wz_region_skip"))}</button>`}
        </div></div></div>`;
    }
    const ready = S.phase === "ready";
    const tried = S.messages.find((m) => m.id === S.ui.tryId);
    return `<div class="wz-body">
      <h2>${esc(t("wz_try_title"))}</h2>
      <div class="lead">${esc(ready ? t("wz_try_body", names) : t("wz_try_wait"))}</div>
      <form class="wz-try" id="wz-try"><input id="wz-say" maxlength="200" value="${esc(t("say_placeholder").replace(/^[^：:]*[：:]\s*/, ""))}" ${ready ? "" : "disabled"}>
        <button class="primary" type="submit" ${ready ? "" : "disabled"}>${esc(t("translate"))}</button></form>
      <div class="wz-result">${tried ? `<div class="msg me" style="max-width:100%;align-items:flex-start"><div class="bubble">${bubbleHTML(tried).replace(/<div class="actions">.*?<\/div>/, "")}</div></div>` : ""}</div>
    </div>`;
  }

  function renderWizard() {
    const el = $("wizard");
    if (S.ui.wz === 0) {
      const need = S.phase === "need_download";
      const tier = S.gpu.tier || "cpu";
      const modeLine = S.mode.quality === "high" ? t("wz_mode_high", { name: S.gpu.name })
        : (tier !== "cpu" && S.gpu.name ? t("wz_mode_fast", { name: S.gpu.name }) : t("wz_mode_cpu"));
      const settled = S.phase !== "starting" && S.phase !== "updating";
      el.innerHTML = `
        <div><h1>${esc(t("wz_welcome"))}</h1><div class="lead" style="margin-top:8px">${esc(t("wz_welcome_body"))}</div></div>
        <section class="wz-facts"><h2>${esc(t("ui_title"))}</h2>${uiLangsHTML()}</section>
        <section class="wz-facts"><h2>${esc(t("trans_title"))}</h2>${pairSelectHTML()}</section>
        <section class="wz-facts">
          ${settled ? `<div class="note">${ICON.info}<span>${esc(modeLine)}<br>${esc(need ? t("wz_need", { gb: gb(S.detail.total_mb) }) : (S.phase === "error" ? t("err_title") : t("wz_ready")))}</span></div>`
            : `<div class="note">${ICON.info}<span>${esc(t("st_starting"))}</span></div>`}
        </section>
        <div class="wz-foot"><span></span><button class="primary big" data-act="wz-begin" ${settled ? "" : "disabled"}>${esc(need ? t("wz_start_download") : t("wz_start"))}</button></div>`;
    } else {
      el.innerHTML = `
        <div><h1>${esc(t("wz_title"))}</h1><div class="lead" style="margin-top:6px;font-size:13px">${esc(t("wz_sub"))}</div></div>
        ${stepsHTML()}
        ${wizardBody()}
        <div class="wz-foot">
          <button class="ghost" data-act="wz-prev">${esc(t("wz_prev"))}</button>
          <div class="dim small">${esc(t("wz_step", { n: S.ui.wz }))}</div>
          ${S.ui.wz < 3 ? `<button class="primary" data-act="wz-next">${esc(t("wz_next"))}</button>`
            : `<button class="primary" data-act="wz-finish">${esc(t("wz_finish"))}</button>`}
        </div>`;
    }
    renderWizardStrip();
    paintLevels();
  }

  function wizardGo(step) {
    const was = S.ui.wz;
    S.ui.wz = Math.max(0, Math.min(3, step));
    const held = $("wizard").contains(document.activeElement) && (document.activeElement.dataset.act || "wz-next");
    renderWizard();
    $("wizard").scrollTop = 0;
    const same = held && $("wizard").querySelector(`.wz-foot [data-act="${held}"]`);      // 用鍵盤一路按「下一步」或「上一步」：焦點留在同一顆上，沒有了就停在主要的按鈕
    const main = same && !same.disabled ? same : $("wizard").querySelector(".wz-foot .primary");
    if (held && main && !main.disabled) main.focus({ preventScroll: true });
    if (S.ui.wz === 1) openDevices();
    else if (was === 1) post("/api/meters", { on: false });
  }

  // ---------------------------------------------------------------- 蓋板

  // 「有新版本」的詢問：開啟時的詢問，和程式開著的時候畫面上小小的提醒按了「看看改了什麼」之後，都是這一張
  function updateAskHTML(d) {
    const notes = d.notes || [];
    return `<h2>${esc(t("up_ask_title", { version: d.version }))}</h2><p>${esc(t("up_ask_text"))}${d.size ? " " + esc(t("up_size", { size: sizeText(d.size) })) : ""}</p>
      ${notes.length ? `<div class="changes" id="up-notes" tabindex="0" role="group" aria-label="${esc(t("up_notes_title"))}"><b>${esc(t("up_notes_title"))}</b>${changesHTML(notes, notes.length > 1)}</div>` : `<div class="note" id="up-notes-missing">${ICON.info}<span>${esc(t("up_notes_missing"))}</span></div>`}
      <div class="btn-row center"><button class="primary" data-act="update-now">${esc(t("up_now"))}</button>
      <button class="ghost" data-act="update-later">${esc(t("up_later"))}</button></div>`;
  }

  // 程式一直開著的時候查到新版本：主畫面上方一條小小的提醒（不擋住任何東西），按了才看這一版改了什麼、才決定要不要更新
  function renderUpdateBanner() {
    const el = $("up-banner"), o = S.updateOffer;
    const show = !!(o && o.version) && S.phase === "ready" && S.view === "main";
    el.hidden = !show;
    el.innerHTML = show ? `${ICON.up}<span>${esc(t("up_ask_title", { version: o.version }))}</span>
      <button class="ghost" data-act="up-review">${esc(t("up_review"))}</button>
      <button class="icon-btn" data-act="update-later" aria-label="${esc(t("close"))}" title="${esc(t("close"))}">${ICON.x}</button>` : "";
  }

  function renderOverlay() {
    const el = $("overlay");
    const d = S.detail || {};
    let html = "", wide = false;      // wide：有一長串「這一版改了什麼」要放的時候，蓋板寬一點
    if (S.phase === "updating") {
      const text = d.step === "downloading" ? t("up_downloading", { version: d.version, percent: d.percent || 0 })
        : (d.step === "installing" ? t("up_installing", { version: d.version }) : t("up_checking"));
      if (d.step === "ask") {
        wide = (d.notes || []).length > 0;
        html = updateAskHTML(d);
      } else {
        const title = d.back ? t("up_back_title") : t("st_updating");
        const line = !d.back ? text : d.step === "downloading" ? t("up_back_downloading", { version: d.version, percent: d.percent || 0 }) : t("up_back_installing", { version: d.version });
        html = `<div class="spinner"></div><h2>${esc(title)}</h2><p>${esc(line)}</p>` +
          (d.step === "downloading" ? `<div class="bar"><span style="width:${d.percent || 0}%"></span></div>
            <div class="btn-row center"><button class="ghost" data-act="update-cancel">${esc(t("cancel"))}</button></div>` : "");
      }
    } else if (S.phase === "error" && !(S.view === "settings" && S.ui.tab === "health")) {      // 在「回報問題」那一頁時蓋板先收起來，才按得到那一頁
      html = `<h2>${esc(t("err_title"))}</h2><p>${esc(d.code ? t("err_" + d.code) : (d.message || ""))}</p>` +
        (d.log ? `<p>${esc(t("err_log", { path: d.log }))}</p>` : "") +
        `<div class="btn-row center"><button class="primary" data-act="retry">${esc(t("retry"))}</button>
          <button class="ghost" data-act="err-report">${esc(t("rep_title"))}</button>
          ${d.log ? `<button class="ghost" data-act="err-logs">${esc(t("sto_open"))}</button>` : ""}</div>`;
    } else if (S.view !== "wizard" && S.phase === "need_download") {
      html = `<h2>${esc(t("st_need_download"))}</h2><p>${esc(d.error ? t("dl_error", { error: d.error }) : t("dl_need", { gb: gb(d.total_mb) }))}</p>
        <button class="primary" data-act="download">${esc(d.error ? t("retry") : t("dl_start"))}</button>` +
        (S.cfg.quality !== "standard" && S.mode.quality === "high" ? `<button class="link" data-act="quality" data-q="standard">${esc(t("q_standard"))}</button>` : "");
    } else if (S.view !== "wizard" && S.phase === "downloading") {
      html = `<h2>${esc(t("dl_title"))}</h2><p>${esc(downloadLine())}</p><div class="bar"><span style="width:${percent()}%"></span></div>`;
    } else if (S.view !== "wizard" && S.phase === "ready" && S.ui.upReview && S.updateOffer) {      // 提醒上按了「看看改了什麼」
      wide = (S.updateOffer.notes || []).length > 0;
      html = updateAskHTML(S.updateOffer);
    } else if (S.view !== "wizard" && S.phase === "ready" && (S.whatsNew || []).length) {      // 更新（或重新安裝）完第一次開：這一版和跳過的每一版改了什麼，按「知道了」才關
      wide = true;
      html = `<h2>${esc(t("wn_title", { version: S.version }))}</h2><p>${esc(t("wn_text"))}</p>
        <div class="changes" id="wn-notes" tabindex="0" role="group" aria-label="${esc(t("up_notes_title"))}">${changesHTML(S.whatsNew, S.whatsNew.length > 1)}</div>
        <div class="btn-row center"><button class="primary" id="wn-done" data-act="wn-done">${esc(t("wn_done"))}</button></div>`;
    }
    el.hidden = !html;
    el.innerHTML = html ? `<div class="panel${wide ? " wide" : ""}" role="alertdialog" aria-modal="true">${html}</div>` : "";
    ["rail", "view-main", "view-settings", "view-wizard"].forEach((id) => { $(id).inert = !!html; });      // 蓋板開著：底下的不能按，Tab 也不會跑到底下
  }

  // ---------------------------------------------------------------- 畫面切換

  function showView(view) {
    S.view = view;
    app.dataset.view = view;
    renderRail();
    if (view === "settings") { renderSettingsPage(); tabOpened(); }
    if (view === "wizard") renderWizard();
    renderOverlay();
    renderUpdateBanner();
  }

  function setView(view) {
    showView(view);
    post("/api/view", { view });
  }

  function renderMicViews() {
    renderMicRow();
    if (S.view === "settings" && S.ui.tab === "audio") renderSettingsPage();
  }

  function renderAll() {
    applyTheme();
    applyI18n();
    renderStatus();
    renderLangs();
    renderRecord();
    renderSources();
    renderMessages();
    renderRail();
    if (S.view === "settings") renderSettingsPage();
    if (S.view === "wizard") renderWizard();
    renderOverlay();
    renderUpdateBanner();
    renderOverlayState();
  }

  // ---------------------------------------------------------------- 按鈕

  const actions = {
    view: (el) => setView(el.dataset.view),
    "toggle-langs": () => { S.ui.langOpen = !S.ui.langOpen; renderLangs(); },
    "set-lang": (el) => saveCfg(el.dataset.side === "their_lang"
      ? { their_lang: el.dataset.code, their_auto: false } : { [el.dataset.side]: el.dataset.code }),
    "set-auto": () => saveCfg({ their_auto: true }),
    source: (el) => {
      const key = el.dataset.key;
      post("/api/source", { key, on: !(S.sources[key] || {}).on });
    },
    // 主畫面和「還沒框過」的按鈕：框好就開始讀；設定頁裡各區域的「重新框選」：只換位置，開關不動
    region: (el) => post("/api/region", { enable: !el.dataset.keep, id: el.dataset.id || undefined }),
    "region-add": () => post("/api/region", { enable: false, new: true }),
    "region-on": (el) => saveRegions(regions().map((r) => (r.id === el.dataset.id ? Object.assign({}, r, { on: !r.on }) : r)), "#region-" + el.dataset.id),
    "region-remove": (el) => saveRegions(regions().filter((r) => r.id !== el.dataset.id), "#regions-section"),
    clear: () => post("/api/clear"),
    fix: (el) => {
      const id = Number(el.dataset.id);
      const m = S.messages.find((x) => x.id === id);
      const slot = $("m-" + id) && $("m-" + id).querySelector(".fixslot");
      if (!m || !slot) return;
      if (slot.innerHTML) { slot.innerHTML = ""; return; }
      document.querySelectorAll(".fixslot").forEach((s) => { s.innerHTML = ""; });
      slot.innerHTML = fixHTML(m);
      slot.scrollIntoView({ block: "nearest" });
      const first = slot.querySelector("input, textarea");
      if (first) first.focus({ preventScroll: true });
    },
    "fix-word": (el) => {
      const input = el.closest(".fix").querySelector(".fix-src");
      input.value = el.dataset.word;
      el.closest(".fix").querySelector(".fix-dst").focus();
    },
    "fix-save": async (el) => {
      const box = el.closest(".fix");
      const src = box.querySelector(".fix-src").value.trim();
      const dst = box.querySelector(".fix-dst").value.trim();
      if (!src || !dst) { (src ? box.querySelector(".fix-dst") : box.querySelector(".fix-src")).focus(); return; }
      const r = await post("/api/fix", { id: Number(el.dataset.id), src, dst });
      if (r.ok) { box.parentElement.innerHTML = ""; toast(t("n_saved")); }
    },
    "edit-save": async (el) => {
      const box = el.closest(".fix");
      const input = box.querySelector(".edit-src");
      const text = input.value.trim();
      const m = S.messages.find((x) => x.id === Number(el.dataset.id));
      if (!text || (m && text === m.original)) { input.focus(); return; }
      const r = await post("/api/edit", { id: Number(el.dataset.id), text });
      if (r.ok) box.parentElement.innerHTML = "";
    },
    "teach-save": async (el) => {
      const box = el.closest(".fix");
      const input = box.querySelector(".teach-dst");
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      const r = await post("/api/teach", { id: Number(el.dataset.id), text });
      if (r.ok) { box.parentElement.innerHTML = ""; toast(t("n_taught")); }
    },
    again: (el) => post("/api/again", { id: Number(el.dataset.id) }),
    copy: async (el) => { const r = await post("/api/copy", { id: Number(el.dataset.id) }); if (r.ok) toast(t("copied"), 1600); },
    tab: (el) => openTab(el.dataset.tab),
    "try-one": () => { const say = $("say"); say.value = t("say_placeholder").replace(/^[^：:]*[：:]\s*/, ""); say.focus(); },
    nav: (el) => {
      const to = el.dataset.to;
      if (to === "main") {
        if (S.view === "settings" && S.ui.tab === "audio") post("/api/meters", { on: false });
        if (S.view !== "main") setView("main");
      } else if (S.view === "settings") openTab(to);
      else { S.ui.tab = to; setView("settings"); }
    },
    device: (el) => {
      const key = el.dataset.key;
      saveCfg(key === "speaker"
        ? { speaker_device: el.dataset.id, speaker_name: el.dataset.name }
        : { mic_device: el.dataset.id, mic_name: el.dataset.name });
    },
    quality: (el) => saveCfg({ quality: el.dataset.q }),
    tone: (el) => saveCfg({ tone: el.dataset.tone }),
    "tone-keep": (el) => saveCfg({ tone_keep: !!el.dataset.value }),
    "speak-test": () => post("/api/speak", { action: "test" }),
    replies: async (el) => {
      const r = await post("/api/replies", { id: Number(el.dataset.id) });
      if (!r || !r.ok) toast(t(r && r.error === "not_ready" ? "n_not_ready" : (r && r.error === "busy" ? "reply_busy" : "reply_none")), 6000, "info");
    },
    "replies-close": () => { S.replies = Object.assign({}, S.replies, { closed: true }); renderMessages(); },
    "reply-copy": async (el) => {
      const it = ((S.replies || {}).items || [])[Number(el.dataset.n)];
      if (!it) return;
      const r = await post("/api/replies", { action: "copy", text: it.text });
      if (r && r.ok) { S.ui.replyCopied = Number(el.dataset.n); renderMessages(); setTimeout(() => { S.ui.replyCopied = null; renderMessages(); }, 2500); }
    },
    "word-add": async (el) => {
      const w = (S.words[Number(el.dataset.id)] || [])[Number(el.dataset.n)];
      if (!w) return;
      const r = await post("/api/words", { action: "add", w: w.w, lang: w.lang, meaning: w.meaning, id: Number(el.dataset.id) });
      if (r && r.ok) { S.ui.wordSaved = Object.assign(S.ui.wordSaved || {}, { [wordKey(w)]: true }); upsertMessage(S.messages.find((m) => m.id === Number(el.dataset.id)) || { id: 0 }); }
    },
    "word-know": async (el) => {
      const w = (S.words[Number(el.dataset.id)] || [])[Number(el.dataset.n)];
      if (w) await post("/api/words", { action: "know", w: w.w, lang: w.lang });
    },
    "word-pick": async () => {      // 在字幕上選取一段字，按浮出來的「收進單字本」
      const pick = S.ui.pick;
      hidePick();
      if (!pick) return;
      const r = await post("/api/words", { action: "add", w: pick.w, lang: pick.lang, id: pick.id });
      toast(r && r.ok ? t("learn_added", { w: pick.w }) : t(r && r.error === "not_ready" ? "n_not_ready" : "learn_add_failed"), 4200, r && r.ok ? "info" : "warn");
    },
    "word-remove": async (el) => { const r = await post("/api/words", { action: "remove", w: el.dataset.w, lang: el.dataset.lang }); if (r && r.ok) loadWords(); },
    "review-start": async () => {
      const r = await get("/api/words?review=1");
      if (!r || !r.items || !r.items.length) return;
      S.ui.review = { items: r.items.slice(0, 30), i: 0, show: false };
      paintReview();
    },
    "review-show": () => { if (S.ui.review) { S.ui.review.show = true; paintReview(); } },
    "review-answer": async (el) => {
      const rv = S.ui.review, w = rv && rv.items[rv.i];
      if (!w) return;
      rv.i += 1; rv.show = false;
      paintReview();
      await post("/api/words", { action: "review", w: w.w, lang: w.lang, remembered: !!el.dataset.value });
      if (rv.i >= rv.items.length) setTimeout(() => { if (S.ui.review === rv) { S.ui.review = null; loadWords(); } }, 2500);
    },
    "recap-now": async () => {
      const r = await post("/api/recaps", { action: "now" });
      if (r && !r.ok) toast(t(r.error === "not_ready" ? "n_not_ready" : (r.error === "busy" ? "recap_thinking" : "recap_nothing")), 6000, "info");
    },
    "recap-remove": async (el) => { const r = await post("/api/recaps", { action: "remove", id: el.dataset.id }); if (r && r.ok) loadRecaps(); },
    "stream-copy": async () => {
      const r = await post("/api/stream", { action: "copy" });
      if (r && r.ok) { S.ui.streamCopied = true; if (S.view === "settings" && S.ui.tab === "overlay") renderSettingsPage(); setTimeout(() => { S.ui.streamCopied = false; }, 3000); }
    },
    app: (el) => {      // 勾或取消一個程式
      const exe = el.dataset.exe, now = (S.cfg.listen_apps || []).slice();
      const has = now.some((c) => c.toLowerCase() === exe.toLowerCase());
      saveCfg({ listen_apps: has ? now.filter((c) => c.toLowerCase() !== exe.toLowerCase()) : now.concat(exe) }, "#" + el.id).then(loadApps);
    },
    "apps-clear": () => saveCfg({ listen_apps: [] }, "#apps-section").then(loadApps),
    "apps-refresh": () => loadApps(),
    "sgpu-download": () => post("/api/speech_gpu", { download: true }),
    "sgpu-cancel": () => post("/api/speech_gpu", { cancel: true }),
    "spk-retry": () => post("/api/speakers", { action: "retry" }),
    "spk-forget": async () => { const r = await post("/api/speakers", { action: "forget" }); if (r && r.ok) toast(t((S.speakers || {}).remember ? "spk_forgot_kept" : "spk_forgot"), 4200); },
    "spk-open": () => { S.ui.tab = "audio"; setView("settings"); setTimeout(() => { const el = document.querySelector('[data-key="tell_speakers"]'); if (el) el.scrollIntoView({ block: "center" }); }, 60); },
    "lib-export": async () => {
      const r = await post("/api/library", { export: true });
      toast(r.ok ? t("lib_exported", r) : t("lib_export_failed", { error: r.error || "" }), 9000, r.ok ? "info" : "warn");
    },
    "lib-import": () => { const f = $("lib-file"); f.value = ""; f.click(); },
    "mem-range": (el) => { S.ui.memRange = el.dataset.value; renderSettingsPage(); const b = document.querySelector(`[data-act="mem-range"][data-value="${el.dataset.value}"]`); if (b) b.focus(); },
    "mem-with-mine": () => { S.ui.memWithMine = !S.ui.memWithMine; renderSettingsPage(); const b = document.querySelector('[data-act="mem-with-mine"]'); if (b) b.focus(); },
    "mem-export": async () => {
      const r = await post("/api/memory", { action: "export", range: S.ui.memRange || "2h", with_mine: !!S.ui.memWithMine });
      toast(r.ok ? t("mem_exported", r) : (r.error === "empty" ? t("mem_export_empty") : t("lib_export_failed", { error: r.error || "" })), 9000, r.ok ? "info" : "warn");
    },
    "mem-import": () => { const f = $("mem-file"); f.value = ""; f.click(); },
    "sub-import": () => { const f = $("sub-file"); f.value = ""; f.click(); },
    "sub-swap": () => subtitleLoad(!S.ui.subSwap),
    "sub-work": (el) => { S.ui.subWork = el.dataset.work; renderSettingsPage(); const b = document.querySelector(`[data-act="sub-work"][data-work="${el.dataset.work}"]`); if (b) b.focus(); },
    "sub-no": () => { S.ui.subPreview = null; S.ui.subFiles = null; renderSettingsPage(); const b = document.querySelector('[data-act="sub-import"]'); if (b) b.focus(); },
    "sub-yes": async () => {
      const p = S.ui.subPreview, now = p && p.now;
      const work = S.ui.subWork != null ? S.ui.subWork : (now ? now.key : "");
      const title = work ? "" : (($("sub-title") || {}).value || "");
      const r = await post("/api/memory", { action: "subtitles_import", files: S.ui.subFiles, swap: !!S.ui.subSwap, work, title, name: S.ui.subName || "" });
      if (r && r.ok) { S.ui.subPreview = null; S.ui.subFiles = null; }
      toast(r && r.ok ? t("sub_done", r) : t("n_" + ((r && r.error) || "sub_unreadable")), 9000, r && r.ok ? "info" : "warn");
      renderSettingsPage();
      const b = document.querySelector('[data-act="sub-import"]'); if (b) b.focus();
    },
    "works-open": () => { S.ui.worksOpen = !S.ui.worksOpen; renderSettingsPage(); const b = document.querySelector('[data-act="works-open"]'); if (b) b.focus(); },
    "work-export": (el) => { S.ui.workExport = el.dataset.work; renderSettingsPage(); const i = $("work-title"); if (i) { i.focus(); i.select(); } },
    "work-export-no": () => { const k = S.ui.workExport; S.ui.workExport = ""; renderSettingsPage(); const b = document.querySelector(`[data-act="work-export"][data-work="${k}"]`); if (b) b.focus(); },
    "work-export-yes": async (el) => {
      const title = ($("work-title") || {}).value || "";
      const r = await post("/api/memory", { action: "export", work: el.dataset.work, title, with_mine: !!S.ui.memWithMine });
      S.ui.workExport = "";
      renderSettingsPage();
      toast(r.ok ? t("mem_exported", r) : (r.error === "empty" ? t("mem_export_empty") : t("lib_export_failed", { error: r.error || "" })), 9000, r.ok ? "info" : "warn");
    },
    "work-own": (el) => post("/api/memory", { action: "own_only", work: el.dataset.work, on: el.getAttribute("aria-pressed") !== "true" }),
    "work-forget": (el) => post("/api/memory", { action: "forget_work", work: el.dataset.work }),
    "mem-pack-remove": (el) => post("/api/memory", { action: "remove_pack", pack: el.dataset.pack }),
    "mem-forget": () => { S.ui.memAsk = true; renderSettingsPage(); const b = document.querySelector('[data-act="mem-forget-no"]'); if (b) b.focus(); },
    "mem-forget-no": () => { S.ui.memAsk = false; renderSettingsPage(); const b = document.querySelector('[data-act="mem-forget"]'); if (b) b.focus(); },
    "mem-forget-yes": async () => {
      const r = await post("/api/memory", { action: "forget" });
      S.ui.memAsk = false;
      if (r && r.ok) toast(t("mem_forgot", r), 4200);
      renderSettingsPage();
      const b = document.querySelector('[data-act="mem-import"]'); if (b) b.focus();
    },
    pack: (el) => {      // 用語包可以多選
      const now = (S.cfg.term_packs || []).slice(), p = el.dataset.pack;
      saveCfg({ term_packs: now.indexOf(p) >= 0 ? now.filter((x) => x !== p) : now.concat(p) }, "#pack-" + p);
    },
    toggle: (el) => saveCfg({ [el.dataset.key]: !S.cfg[el.dataset.key] }),
    set: (el) => saveCfg({ [el.dataset.key]: el.dataset.value }),
    "overlay-preset": async (el) => { const r = await post("/api/overlay", { preset: el.dataset.code }); if (r.ok && r.saved) flashSaved("preset-" + el.dataset.code); loadScreens(); },
    "overlay-look-reset": async () => { const r = await post("/api/overlay", { reset_look: true }); if (r.ok && r.saved) flashSaved("overlay_look"); },
    "find-mic": () => { S.ui.find = { state: "listening" }; renderMicViews(); post("/api/find_mic"); },
    "find-dismiss": () => { S.ui.find = null; renderMicViews(); },
    record: () => { if (!S.record.saving) post("/api/record", { on: !S.record.on }); },
    "rec-open": async (el) => {
      const r = await post("/api/record", { open: el.dataset.what });
      if (!r.ok) toast(t("rec_open_failed", { path: el.dataset.what === "file" ? S.record.file : S.record.folder }), 9000, "warn");
    },
    "rec-dismiss": () => { S.ui.recHidden = true; renderRecord(); },
    "rec-choose": () => post("/api/record", { choose: true }),
    "update-now": () => { S.ui.upReview = false; post("/api/update", { choice: "now" }); },
    "update-later": () => { S.ui.upReview = false; post("/api/update", { choice: "later" }); renderOverlay(); },
    "up-review": () => { S.ui.upReview = true; renderOverlay(); },
    "wn-done": () => { S.whatsNew = []; renderOverlay(); post("/api/seen", { whats_new: true }); },
    "update-cancel": () => post("/api/update", { choice: "cancel" }),
    "find-cancel": () => post("/api/find_mic", { cancel: true }),
    "find-undo": () => {
      const prev = S.ui.find && S.ui.find.previous;
      S.ui.find = null;
      renderMicViews();
      if (prev) post("/api/config", { mic_device: prev.id, mic_name: prev.name });
    },
    "mic-settings": () => post("/api/mic_settings"),
    "gloss-remove": (el) => post("/api/glossary/remove", { src: el.dataset.src, game: el.dataset.game || "" }),
    "lesson-remove": (el) => post("/api/lessons/remove", { src: el.dataset.src, target: el.dataset.target }),
    "ocr-test": async (el) => {
      S.ui.ocrId = el.dataset.id || "";
      S.ui.ocr = "reading"; renderSettingsPage();
      S.ui.ocr = await post("/api/ocr_test", { id: S.ui.ocrId || undefined }) || { ok: false, error: "no response" }; renderSettingsPage();
    },
    "ui-lang": (el) => saveCfg({ ui_lang: el.dataset.code }),
    theme: (el) => saveCfg({ theme: el.dataset.code }),
    accent: (el) => saveCfg({ accent: el.dataset.code }),
    swap: () => saveCfg({ my_lang: S.cfg.their_lang, their_lang: S.cfg.my_lang, their_auto: false }),
    "rerun-setup": () => { S.ui.wz = 0; setView("wizard"); },
    "set-export": async () => {
      const r = await post("/api/settings", { export: true });
      toast(r && r.ok ? t("set_exported", r) : t("set_export_failed", { error: (r && r.error) || "" }), 9000, r && r.ok ? "info" : "warn");
    },
    "set-import": () => { const f = $("set-file"); f.value = ""; f.click(); },
    "set-reset": () => { S.ui.resetAsk = true; renderSettingsPage(); const b = document.querySelector('[data-act="set-reset-no"]'); if (b) b.focus(); },
    "set-reset-no": () => { S.ui.resetAsk = false; renderSettingsPage(); const b = document.querySelector('[data-act="set-reset"]'); if (b) b.focus(); },
    "set-reset-yes": async () => {
      const r = await post("/api/settings", { reset: true });
      S.ui.resetAsk = false;
      settingsApplied(r, "set_reset_done");
    },
    "set-undo": async () => settingsApplied(await post("/api/settings", { undo: true }), "set_undone"),
    "set-forget": async () => { const r = await post("/api/settings", { forget: true }); if (r && r.ok) S.settingsBackup = null; renderSettingsPage(); },
    "store-open": () => post("/api/storage", { open: true }),
    "store-choose": async () => storeAnswer(await post("/api/storage", { choose: true })),
    "store-back": async () => storeAnswer(await post("/api/storage", { back: true })),
    "store-cancel": () => post("/api/storage", { cancel: true }),
    "store-remove": (el) => { S.ui.storeAsk = el.dataset.id; renderSettingsPage(); const b = document.querySelector('[data-act="store-remove-no"]'); if (b) b.focus(); },
    "store-remove-no": (el) => { S.ui.storeAsk = ""; renderSettingsPage(); const b = document.querySelector(`[data-act="store-remove"][data-id="${el.dataset.id}"]`); if (b) b.focus(); },
    "store-remove-yes": async (el) => {
      const r = await post("/api/storage", { remove: el.dataset.id });
      S.ui.storeAsk = "";
      if (r && r.ok) toast(t("sto_removed", { size: sizeText(r.freed) }), 6000);
      else storeAnswer(r);
      await loadStorage();
    },
    rollback: () => { S.ui.backAsk = true; renderSettingsPage(); const b = document.querySelector('[data-act="rollback-no"]'); if (b) b.focus(); },
    "rollback-no": () => { S.ui.backAsk = false; renderSettingsPage(); const b = document.querySelector('[data-act="rollback"]'); if (b) b.focus(); },
    "rollback-yes": async () => {
      S.ui.backAsk = false;
      const r = await post("/api/update", { rollback: true });
      if (r && !r.ok) { toast(t("n_" + r.error, r), 9000, "warn"); renderSettingsPage(); }
    },
    "update-unskip": async () => {
      const r = await post("/api/update", { unskip: true });
      if (r && r.ok) { S.update = r; toast(t("up_unskipped"), 5000); renderSettingsPage(); }
    },
    "report-part": (el) => { S.ui.reportParts[el.dataset.key] = !S.ui.reportParts[el.dataset.key]; renderSettingsPage(); },
    "report-make": async () => {
      const note = $("rep-note") ? $("rep-note").value : "";
      S.ui.reportNote = note;
      const r = await post("/api/report", { note, settings: S.ui.reportParts.settings, log: S.ui.reportParts.log });
      if (!r || !r.ok) return;
      S.ui.report = r;
      renderSettingsPage();
      const box = $("rep-text");
      if (box) { box.focus(); box.scrollIntoView({ block: "nearest" }); }
    },
    "report-back": () => {
      S.ui.report = null;
      renderSettingsPage();
      const box = $("rep-note");
      if (box) { box.value = S.ui.reportNote || ""; box.focus(); }
    },
    "report-save": async () => {
      const r = await post("/api/report", { save: true });
      if (!r || !r.ok) { toast(t(r && r.error === "report_none" ? "n_report_none" : "n_report_failed", { error: (r && r.detail) || "" }), 8000, "warn"); return; }
      if (S.ui.report) Object.assign(S.ui.report, { saved: r.name, folder: r.folder });
      renderSettingsPage();
      const note = $("rep-saved");
      if (note) note.scrollIntoView({ block: "nearest" });
    },
    "report-copy": async () => {
      const r = await post("/api/report", { copy: true });
      toast(t(r && r.ok ? "rep_copied" : "n_report_none"), 5000, r && r.ok ? "info" : "warn");
    },
    "friend-lang": (el) => { S.ui.friendLang = el.dataset.code; S.ui.friendSaved = null; renderSettingsPage(); },
    "friend-save": async () => {
      const r = await post("/api/help", { friend: S.ui.friendLang || (S.ui.help || {}).friend_lang || S.cfg.ui_lang });
      if (!r || !r.ok) { toast(t("n_help_failed", { error: (r && r.detail) || "" }), 8000, "warn"); return; }
      S.ui.friendSaved = r;
      renderSettingsPage();
      const note = $("help-saved");
      if (note) note.scrollIntoView({ block: "nearest" });
    },
    "use-clear": () => { S.ui.useAsk = true; renderSettingsPage(); const b = document.querySelector('[data-act="use-clear-no"]'); if (b) b.focus(); },
    "use-clear-no": () => { S.ui.useAsk = false; renderSettingsPage(); const b = document.querySelector('[data-act="use-clear"]'); if (b) b.focus(); },
    "use-clear-yes": async () => {
      const r = await post("/api/usage", { clear: true });
      S.ui.useAsk = false;
      if (r && r.ok) { S.ui.usage = r; toast(t("use_cleared"), 4200); }
      renderSettingsPage();
      const b = document.querySelector('[data-act="toggle"][data-key="usage_keep"]'); if (b) b.focus();
    },
    "health-again": async () => { loadUsage(); await loadHealth(); const b = document.querySelector('[data-act="health-again"]'); if (b) b.focus(); },
    "hist-more": () => loadHistory(true),
    "hist-export": async () => {
      const r = await post("/api/history", { export: true, q: (S.ui.hist && S.ui.hist.q) || "" });
      if (r && r.ok) toast(t("hist_exported", { n: r.count, name: r.name }), 7000);
      else toast(t("hist_export_failed", { error: (r && r.error) || "" }), 8000, "error");
    },
    "hist-clear": () => { S.ui.histConfirm = true; paintHistory(); const b = document.querySelector('[data-act="hist-clear-no"]'); if (b) b.focus(); },
    "hist-clear-no": () => { S.ui.histConfirm = false; paintHistory(); const b = document.querySelector('[data-act="hist-clear"]'); if (b) b.focus(); },
    "hist-clear-yes": async () => {
      const r = await post("/api/history", { clear: true });
      S.ui.histConfirm = false;
      if (r && r.ok) toast(t("hist_cleared", { n: r.removed }), 5000);
      else toast(t("hist_clear_failed", { error: (r && r.error) || "" }), 8000, "error");
      if (S.ui.hist) S.ui.hist.q = "";
      const input = $("hist-q"); if (input) input.value = "";
      loadHistory();
    },
    "hotkey-set": (el) => hotkeyRecord(S.ui.hotkeyRec === el.dataset.name ? null : el.dataset.name),
    "hotkey-clear": (el) => saveHotkey(el.dataset.name, ""),
    "hotkey-reset": () => saveCfg({ hotkeys: S.hotkeys.defaults || {} }),
    "phrase-add": async () => { const el = $("phrase-new"); const text = el ? el.value.trim() : ""; if (text) await phraseDo({ action: "add", text }); },
    "phrase-starters": () => phraseDo({ action: "starters" }),
    "phrase-remove": (el) => phraseDo({ action: "remove", id: el.dataset.id }),
    "quick-try": async () => { const r = await post("/api/quick_input"); if (r && !r.ok && S.phase === "ready") toast(t("n_save_failed", { error: "" }), 5000, "warn"); },
    "wz-begin": () => { if (S.phase === "need_download") post("/api/download"); wizardGo(1); },
    "wz-next": () => wizardGo(S.ui.wz + 1),
    "wz-prev": () => wizardGo(S.ui.wz - 1),
    "wz-region": () => post("/api/region", { enable: false }),
    "wz-finish": async () => {
      await post("/api/config", { setup_done: true });
      setView("main");
      if (regions().length) post("/api/source", { key: "screen", on: true });
    },
    "overlay-toggle": async () => {
      const on = !S.cfg.overlay;
      await post("/api/config", { overlay: on });
      if (on) { toast(t("n_overlay_on"), 5000); post("/api/overlay", { test: true }); }
    },
    "display-mode": (el) => saveCfg(el.dataset.code === "window" ? { overlay: false } : { overlay: true, overlay_form: el.dataset.code }, "#display-mode").then(loadScreens),
    "map-monitor": async (el) => { const r = await post("/api/overlay", { monitor: Number(el.dataset.code) }); if (r && r.ok && r.saved) flashSaved("overlay_map"); loadScreens(); },
    "layout-apply": async (el) => { const r = await post("/api/overlay", { layout: Number(el.dataset.code) }); if (r && r.ok && r.saved) flashSaved("#layout-" + el.dataset.code); loadScreens(); },
    "layout-remove": async (el) => { const r = await post("/api/overlay", { layout_remove: Number(el.dataset.code) }); if (r && r.ok && r.saved) flashSaved("#layouts-section"); },
    "overlay-edit": () => post("/api/overlay", { edit: !S.overlay.editing }),
    "overlay-test": () => post("/api/overlay", { test: true }),
    "overlay-reset": () => post("/api/overlay", { reset: true }).then(loadScreens),
    download: () => post("/api/download"),
    retry: () => post("/api/retry"),
    // 出錯畫面：到「回報問題」（蓋板在那一頁會先收起來）、打開放紀錄檔的資料夾
    "err-report": () => { S.ui.goReport = true; S.ui.tab = "health"; setView("settings"); },
    "err-logs": () => post("/api/storage", { open_logs: true })
  };

  // 在字幕上選取一段對方語言的字：旁邊浮出「收進單字本」（中文、泰文這些不會自動標生字的語言也能收）
  function hidePick() { S.ui.pick = null; const b = $("word-pick"); if (b) b.hidden = true; }
  document.addEventListener("mouseup", (e) => {
    if (e.target.closest("#word-pick")) return;
    setTimeout(() => {
      const sel = window.getSelection(), text = sel ? sel.toString().replace(/\s+/g, " ").trim() : "";
      const node = sel && sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
      const part = node && node.closest("#messages .orig, #messages .text");
      const box = part && part.closest(".msg");
      const m = box && S.messages.find((x) => "m-" + x.id === box.id);
      const lang = m && (part.classList.contains("orig") ? m.lang : (S.langs || {})[m.target] || m.target);
      const mine = (S.langs || {})[S.cfg.my_lang] || S.cfg.my_lang;
      if (!m || !text || text.length > 40 || !lang || lang === mine) { hidePick(); return; }
      S.ui.pick = { w: text, lang, id: m.id };
      let b = $("word-pick");
      if (!b) {
        b = document.createElement("button");
        b.id = "word-pick"; b.className = "word-pick"; b.dataset.act = "word-pick";
        document.body.appendChild(b);
      }
      b.textContent = t("learn_pick");
      const r = sel.getRangeAt(0).getBoundingClientRect();
      b.style.left = Math.max(8, Math.min(window.innerWidth - 180, r.left)) + "px";
      b.style.top = Math.max(8, r.top - 38) + "px";
      b.hidden = false;
    }, 0);
  });
  document.addEventListener("scroll", hidePick, true);

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const fn = actions[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el); }
  });

  // ---------------------------------------------------------------- 鍵盤：畫面重畫之後，焦點不能掉
  // 這裡的畫面大多是整塊換掉重畫的。換掉的那一塊裡如果正好有焦點，焦點就會掉回最上面，只用鍵盤的人得從頭再 Tab 一次。
  // 所以記住最後停在哪個東西上；它被換掉了，就在新的畫面裡找回同一個；找不到（例如那一列被刪了）就停在最近的地方。

  const FOCUS_DATA = ["key", "value", "code", "q", "tone", "id", "pack", "exe", "work", "side", "to", "src", "target", "view", "name"];
  const FOCUSABLE = 'button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
  let focusLast = null;      // 最後停的地方 { el, home, exact, loose, index }
  let focusLost = null;      // 原來的地方暫時不見了，先停在替代的地方 { mark, stand, at, wait }
  let focusMine = false;     // 這一次移動焦點是這段程式做的，不是使用者

  const canFocus = (el) => !!el && el.isConnected && !el.disabled && el.getClientRects().length > 0 && !el.closest("[inert]");
  function focusMark(el) {
    const loose = el.dataset.act ? `[data-act="${el.dataset.act}"]` : el.name ? `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]` : el.tagName.toLowerCase();
    const exact = el.id ? "#" + CSS.escape(el.id) : loose + FOCUS_DATA.filter((k) => el.dataset[k] != null).map((k) => `[data-${k}="${CSS.escape(el.dataset[k])}"]`).join("");
    const homes = [];      // 外面一層一層有 id 的區塊，由近到遠；在每一層裡它是同一種按鈕的第幾個
    for (let box = el.parentElement && el.parentElement.closest("[id]"); box && box !== app && homes.length < 4; box = box.parentElement && box.parentElement.closest("[id]")) {
      homes.push({ sel: "#" + CSS.escape(box.id), index: Math.max(0, [...box.querySelectorAll(loose)].indexOf(el)) });
    }
    return { el, homes, exact, loose };
  }
  function focusFind(f, same) {
    const first = (box) => [...box.querySelectorAll(FOCUSABLE)].find(canFocus) || null;
    const shown = (h) => { const box = document.querySelector(h.sel); return box && box.getClientRects().length && !box.closest("[inert]") ? box : null; };
    if (same) {      // 同一個東西（重畫之後是新的元素，但是同一顆按鈕）
      const h = f.homes[0], home = h ? document.querySelector(h.sel) : app;
      if (!home) return null;
      const hits = [...home.querySelectorAll(f.exact)].filter(canFocus);
      if (hits.length < 2) return hits[0] || null;
      const nth = [...home.querySelectorAll(f.loose)][h ? h.index : 0];
      return hits.indexOf(nth) >= 0 ? nth : hits[0];
    }
    if (!$("overlay").hidden) return first($("overlay"));      // 蓋板開著：只能停在蓋板裡
    for (const h of f.homes) {      // 最近一層還在的區塊：同一種按鈕裡最近的那個（刪掉一列，就停在下一列的同一顆），沒有就這一區的第一個
      const home = shown(h);
      if (!home) continue;
      const kin = [...home.querySelectorAll(f.loose)].filter(canFocus);
      const hit = kin[Math.min(h.index, kin.length - 1)] || first(home);
      if (hit) return hit;
    }
    const page = S.view === "settings" ? $("settings-page") : S.view === "wizard" ? $("wizard") : $("view-main");      // 整頁換掉了：新的那一頁的第一個
    return first(page) || document.querySelector('.rail-item[aria-current="page"]');
  }
  function focusPut(el, same) {
    focusMine = true;
    el.focus({ preventScroll: same });      // 找回同一個的時候畫面不要跳；換地方停的時候要捲到看得見
    focusMine = false;
  }
  function focusRepair() {
    const active = document.activeElement;
    const adrift = !active || active === document.body || !canFocus(active);
    if (focusLost && (adrift || active === focusLost.stand)) {      // 原來的那個回來了（例如按鈕忙完又可以按了）就回去
      const back = focusFind(focusLost.mark, true);
      if (back) { focusLost = null; focusPut(back, true); return; }
      if (!focusLost.wait && Date.now() - focusLost.at > 8000) focusLost = null;
    }
    if (!adrift || !focusLast) return;
    const f = focusLast;
    if (f.el === active ? false : canFocus(f.el)) return;      // 東西還在，是使用者自己把焦點移開的
    const again = focusFind(f, true);
    if (again) { focusPut(again, true); return; }
    const stand = focusFind(f, false);
    if (!focusLost) focusLost = { mark: f, at: Date.now(), wait: !$("overlay").hidden };
    focusLost.stand = stand;
    if (stand) focusPut(stand, false);
  }
  document.addEventListener("focusin", (e) => {
    if (!focusMine) focusLost = null;
    if (e.target && e.target !== document.body && e.target.closest("#app")) focusLast = focusMark(e.target);
  });
  document.addEventListener("pointerdown", () => { focusLast = null; focusLost = null; }, true);      // 用滑鼠點別的地方：照滑鼠的
  new MutationObserver(focusRepair).observe(app, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "hidden", "inert", "data-view"] });

  // Ctrl+1 到 Ctrl+9 換頁；在左邊導覽上可以用上下鍵
  document.addEventListener("keydown", (e) => {
    if (S.ui.hotkeyRec) return;
    const items = [...document.querySelectorAll("#rail .rail-item")].filter(canFocus);
    if (e.key === "F1" && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {      // F1：說明
      const help = items.find((el) => el.dataset.to === "help");
      if (help) { e.preventDefault(); help.focus(); help.click(); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && /^[1-9]$/.test(e.key) && items[Number(e.key) - 1]) {
      e.preventDefault();
      items[Number(e.key) - 1].focus();
      items[Number(e.key) - 1].click();
      return;
    }
    const at = items.indexOf(document.activeElement);
    if (at >= 0 && e.key === "ArrowRight") {      // 從導覽直接跳進右邊那一頁的第一個東西
      const page = S.view === "settings" ? $("settings-page") : $("view-main");
      const first = [...page.querySelectorAll(FOCUSABLE)].find(canFocus);
      if (first) { e.preventDefault(); first.focus(); }
      return;
    }
    const to = at < 0 ? -1 : { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[e.key];
    if (to == null || to < 0 || !items[to]) return;
    e.preventDefault();
    items[to].focus();
  });

  let liveTimer = null, livePatch = null;
  function patchOf(el) {      // 一個控制項現在的值，寫成要送去存的樣子
    const value = el.type === "range" ? Number(el.value) : el.value;
    return { [el.dataset.change]: el.dataset.sub ? { [el.dataset.sub]: value } : value };
  }
  document.addEventListener("input", (e) => {
    if (e.target.id === "hist-q") {      // 歷史的搜尋：邊打邊找，停一下才查（摘要也一起找）
      const q = e.target.value.trim();
      clearTimeout(histTimer);
      histTimer = setTimeout(() => { S.ui.hist = Object.assign(S.ui.hist || { items: [], total: 0, all: 0, more: false }, { q }); S.ui.histConfirm = false; loadHistory(); loadRecaps(); }, 220);
      return;
    }
    if (e.target.id === "word-q") {
      const q = e.target.value.trim();
      clearTimeout(wordTimer);
      wordTimer = setTimeout(() => { S.ui.words = Object.assign(S.ui.words || { items: [] }, { q }); loadWords(); }, 220);
      return;
    }
    const key = e.target.dataset && e.target.dataset.range;
    const out = key && $("out-" + key);
    if (out) out.textContent = RANGE_TEXT[key](Number(e.target.value));
    const live = key || (e.target.dataset && e.target.dataset.live);
    if (!live) return;
    S.ui.live = live;      // 拖滑桿、挑顏色的時候就即時套用（浮動留言和預覽跟著變），這段期間設定頁不重畫，不然控制項會被換掉
    livePatch = patchOf(e.target);
    if (!liveTimer) liveTimer = setTimeout(() => { liveTimer = null; if (livePatch) post("/api/config", livePatch); livePatch = null; }, 90);
  });

  document.addEventListener("change", (e) => {
    if (e.target.dataset && e.target.dataset.speaker) {      // 幫說話者取名字：改完（離開輸入框或按 Enter）就存
      const input = e.target;
      post("/api/speakers", { action: "rename", id: Number(input.dataset.speaker), name: input.value }).then((r) => {
        if (r && r.ok) { input.value = r.name; flashSaved("#" + input.id); } else toast(t("n_save_failed", { error: "" }), 6000, "error");
      });
      return;
    }
    if (e.target.dataset && e.target.dataset.spkLang) {      // 這個人說的語言：主語言、副語言一起送，程式整理過再照回來的顯示
      const id = Number(e.target.dataset.spkLang), main = $("spk-lang-" + id), second = $("spk-lang2-" + id), changed = e.target;
      post("/api/speakers", { action: "language", id, lang: main ? main.value : "", lang2: second ? second.value : "" }).then((r) => {
        if (!r || !r.ok) { toast(t("n_save_failed", { error: "" }), 6000, "error"); return; }
        if (main) main.value = r.lang;
        if (second) { second.value = r.lang2; second.disabled = !r.lang; }
        flashSaved("#" + changed.id);
      });
      return;
    }
    if (e.target.id === "set-file") {      // 匯入設定檔：在這裡讀檔，內容交給程式檢查
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      file.text().then((text) => { let data = null; try { data = JSON.parse(text); } catch (ex) { data = null; } return post("/api/settings", { data }); })
        .then((r) => settingsApplied(r, "set_imported"))
        .catch(() => toast(t("set_bad"), 9000, "warn"));
      return;
    }
    if (e.target.id === "mem-file") {      // 匯入朋友的翻譯分享檔：在這裡讀檔，內容交給程式檢查
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      file.text().then((text) => { let data = null; try { data = JSON.parse(text); } catch (ex) { data = null; } return post("/api/memory", { action: "import", data, name: file.name }); })
        .then((r) => toast(r.ok ? t("mem_imported", r) + (r.works ? " " + t("mem_imported_works", r) : "") : t(r.error === "is_library" ? "mem_is_library" : "mem_bad"), 9000, r.ok ? "info" : "warn"))
        .catch(() => toast(t("mem_bad"), 9000, "warn"));
      return;
    }
    if (e.target.id === "gloss-game") { S.ui.glossGame = e.target.value; return; }      // 只記下來，加詞的時候才用
    if (e.target.id === "speak-device") {
      const sel = e.target, name = sel.value ? sel.options[sel.selectedIndex].text : "";
      saveCfg({ speak_device: sel.value || null, speak_name: name }, "#speak-device");
      return;
    }
    if (e.target.id === "sub-file") {      // 字幕檔：一個雙語的，或原文、翻譯各一個；在這裡讀檔，配對交給程式
      const all = [...(e.target.files || [])];
      if (!all.length) return;
      if (all.length > 2) toast(t("n_sub_two_max"), 7000, "warn");
      Promise.all(all.slice(0, 2).map((f) => f.text().then((text) => ({ name: f.name, text }))))
        .then((files) => { S.ui.subFiles = files; S.ui.subName = files[0].name.replace(/\.(srt|vtt)$/i, "").replace(/[._-](zh|ja|ko|en|vi|th|id|es|pt|ru|de|fr)([-_][a-z]{2,4})?$/i, ""); S.ui.subWork = null; return subtitleLoad(false); })
        .catch(() => toast(t("n_sub_unreadable"), 9000, "warn"));
      return;
    }
    if (e.target.id === "lib-file") {      // 匯入詞彙檔：在這裡讀檔，內容交給程式檢查和合併
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      file.text().then((text) => { let data = null; try { data = JSON.parse(text); } catch (ex) { data = null; } return post("/api/library", { data }); })
        .then((r) => toast(r.ok ? t("lib_imported", r) : t(r.error === "is_pack" ? "lib_is_pack" : "lib_bad"), 9000, r.ok ? "info" : "warn"))
        .catch(() => toast(t("lib_bad"), 9000, "warn"));
      return;
    }
    if (e.target.dataset && e.target.dataset.phraseEdit) {      // 自己改這一句的譯文
      post("/api/phrases", { action: "edit", id: e.target.dataset.phraseEdit, text: e.target.value }).then((r) => { if (r && r.items) S.phrases = r; });
      return;
    }
    const key = e.target.dataset && e.target.dataset.change;
    if (!key) return;
    if (key === "region-name" || key === "region-app") {
      const id = e.target.dataset.id, field = key === "region-name" ? "name" : "app";
      saveRegions(regions().map((r) => (r.id === id ? Object.assign({}, r, { [field]: e.target.value }) : r)), "#region-" + id);
      return;
    }
    if (e.target.dataset.range || e.target.dataset.live) {      // 放開滑桿、選好顏色：這才算存好
      clearTimeout(liveTimer); liveTimer = null; livePatch = null; S.ui.live = null;
      saveCfg(patchOf(e.target), e.target.id ? "#" + e.target.id : key);
    } else if (key === "their_lang") saveCfg(e.target.value === "auto" ? { their_auto: true } : { their_lang: e.target.value, their_auto: false }, key);
    else saveCfg({ [key]: e.target.value }, key);
  });
  document.addEventListener("pointerup", () => { if (S.ui.live) setTimeout(() => { S.ui.live = null; }, 300); });

  // 螢幕圖上的方框：拖著移動，拖右下角改大小；方向鍵移動，Shift + 方向鍵改大小
  function mapClamp(r) {
    const sc = S.ui.screens, b = mapBounds(), min = sc.min || [240, 110];
    const w = Math.max(min[0], Math.min(Math.round(r[2]), b.w)), h = Math.max(min[1], Math.min(Math.round(r[3]), b.h));
    return [Math.max(b.x, Math.min(Math.round(r[0]), b.x + b.w - w)), Math.max(b.y, Math.min(Math.round(r[1]), b.y + b.h - h)), w, h];
  }
  document.addEventListener("pointerdown", (e) => {
    const box = e.target.closest && e.target.closest("#map-box");
    if (!box || box.classList.contains("bar") || !S.ui.screens) return;
    e.preventDefault();
    box.focus();
    const map = $("screen-map"), b = mapBounds(), k = b.w / map.clientWidth;      // 圖上一個像素等於螢幕上幾個像素
    const start = S.ui.screens.rect.slice(), sx = e.clientX, sy = e.clientY, resize = !!e.target.closest(".grip");
    S.ui.mapDrag = true;
    box.setPointerCapture(e.pointerId);
    const at = (ev) => {
      const dx = (ev.clientX - sx) * k, dy = (ev.clientY - sy) * k;
      return mapClamp(resize ? [start[0], start[1], start[2] + dx, start[3] + dy] : [start[0] + dx, start[1] + dy, start[2], start[3]]);
    };
    const move = (ev) => mapPlace(at(ev), false);
    const up = (ev) => {
      box.removeEventListener("pointermove", move); box.removeEventListener("pointerup", up); box.removeEventListener("pointercancel", up);
      S.ui.mapDrag = false;
      mapPlace(at(ev), true);
    };
    box.addEventListener("pointermove", move); box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up);
  });
  let mapKeyTimer = null;
  document.addEventListener("keydown", (e) => {
    if (!e.target || e.target.id !== "map-box" || !S.ui.screens) return;
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!step) return;
    e.preventDefault();
    const r = S.ui.screens.rect, d = 20;
    const next = mapClamp(e.shiftKey ? [r[0], r[1], r[2] + step[0] * d, r[3] + step[1] * d] : [r[0] + step[0] * d, r[1] + step[1] * d, r[2], r[3]]);
    S.ui.mapDrag = true;      // 連按的時候先不要重畫整頁，不然焦點會跑掉
    mapPlace(next, false);
    clearTimeout(mapKeyTimer);
    mapKeyTimer = setTimeout(() => { S.ui.mapDrag = false; mapPlace(S.ui.screens.rect, true); }, 500);
  });

  document.addEventListener("keydown", (e) => {      // 句庫的輸入框：Enter 就加進去
    if (e.key === "Enter" && e.target && e.target.id === "phrase-new") { e.preventDefault(); const text = e.target.value.trim(); if (text) phraseDo({ action: "add", text }); }
  });
  document.addEventListener("keydown", (e) => {      // 正在等新的快速鍵：這一下按鍵就是答案，不要讓它做別的事
    const name = S.ui.hotkeyRec;
    if (!name) return;
    if (e.key === "Tab") { hotkeyRecord(null); return; }
    e.preventDefault(); e.stopPropagation();
    if (["Control", "Alt", "Shift", "Meta", "OS"].includes(e.key)) return;
    const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
    if (e.key === "Escape" && !mods.length) { hotkeyRecord(null); return; }
    if ((e.key === "Backspace" || e.key === "Delete") && !mods.length) { saveHotkey(name, ""); return; }
    const code = e.code || "";
    const key = /^Key[A-Z]$/.test(code) ? code.slice(3) : /^Digit\d$/.test(code) ? code.slice(5) : /^F([1-9]|1[0-2])$/.test(code) ? code : "";
    if (!key) { toast(t("hk_bad_key"), 5000, "warn"); return; }
    if (!mods.length) { toast(t("hk_need_mod"), 5000, "warn"); return; }
    saveHotkey(name, [...mods, key].join("+"));
  }, true);
  document.addEventListener("focusout", (e) => {      // 點到別的地方：不等了
    const name = S.ui.hotkeyRec;
    if (!name || !e.target || e.target.id !== "hk-" + name) return;
    setTimeout(() => { if (S.ui.hotkeyRec === name && (document.activeElement || {}).id !== "hk-" + name) hotkeyRecord(null); }, 60);
  });

  document.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    if (form.id === "say-form") {
      const input = $("say");
      const text = input.value.trim();
      if (!text) return;
      if (S.phase !== "ready") { toast(t("n_not_ready"), 4200, "warn"); return; }
      input.value = "";
      post("/api/send", { text });
    } else if (form.id === "store-form") {      // 沒有視窗可以選資料夾的時候（用瀏覽器開）：直接打路徑
      const path = form.elements.path.value.trim();
      if (!path) { form.elements.path.focus(); return; }
      storeAnswer(await post("/api/storage", { move: path }));
    } else if (form.id === "layout-form") {
      const name = form.elements.name.value.trim();
      if (!name) { toast(t("lay_need_name"), 4200, "warn"); form.elements.name.focus(); return; }
      const r = await post("/api/overlay", { layout_save: name });
      if (r && r.ok && r.saved) {      // 存好時頁面已經重畫過，要清的是現在畫面上的那個輸入框
        const input = $("layout-name"); if (input) input.value = "";
        flashSaved("#layout-" + r.index);
      }
      else if (r && r.error === "full") toast(t("lay_full", { max: 5 }), 7000, "warn");
      else toast(t("n_save_failed", { error: "" }), 8000, "error");
    } else if (form.id === "gloss-form") {
      const src = form.elements.src.value.trim(), dst = form.elements.dst.value.trim();
      if (!src || !dst) { (src ? form.elements.dst : form.elements.src).focus(); return; }
      const sel = $("gloss-game");
      const r = await post("/api/glossary/add", { src, dst, game: sel ? sel.value : "" });
      if (r.ok) { form.reset(); form.elements.src.focus(); }
    } else if (form.id === "wz-try") {
      const text = $("wz-say").value.trim();
      if (!text || S.phase !== "ready") return;
      S.ui.tryId = "next";
      post("/api/send", { text });
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (S.ui.langOpen) { S.ui.langOpen = false; renderLangs(); if ($("lang-picker").contains(document.activeElement) || document.activeElement === document.body) $("lang-chip").focus(); }
    document.querySelectorAll(".fixslot").forEach((s) => {
      const back = s.contains(document.activeElement) && s.closest(".msg").querySelector('[data-act="fix"]');
      s.innerHTML = "";
      if (back) back.focus();
    });
    if (S.ui.resetAsk && S.view === "settings") actions["set-reset-no"]();      // 還在問「確定嗎」：Esc 就是不要
    if (S.ui.histConfirm && S.view === "settings") actions["hist-clear-no"]();
    if (S.ui.report && S.view === "settings" && S.ui.tab === "health") actions["report-back"]();
    if (S.ui.useAsk && S.view === "settings" && S.ui.tab === "health") actions["use-clear-no"]();
    if (S.ui.storeAsk && S.view === "settings") actions["store-remove-no"]({ dataset: { id: S.ui.storeAsk } });
    if (S.ui.backAsk && S.view === "settings") actions["rollback-no"]();
  });

  // ---------------------------------------------------------------- 背景送來的事件

  const handlers = {
    phase: (d) => {
      const before = S.phase;
      S.phase = d.phase; S.detail = d.detail || {};
      if (d.mode) S.mode = d.mode;
      if (d.gpu) S.gpu = d.gpu;
      if (typeof d.fallback === "boolean") S.fallback = d.fallback;
      renderStatus(); renderOverlay(); renderUpdateBanner();
      if (S.view === "wizard") { if (before !== S.phase) renderWizard(); else renderWizardStrip(); }
      if (S.view === "settings" && S.ui.tab === "quality" && before !== S.phase) renderSettingsPage();
      if (before !== S.phase) healthSoon();
    },
    source: (d) => {
      const before = S.sources[d.key] || {};
      S.sources[d.key] = d;
      renderSources();
      if (before.on !== d.on || before.error !== d.error || before.quiet !== d.quiet || before.lost !== d.lost) healthSoon();
      if (d.key === "mic" && (d.quiet || "") !== (before.quiet || "") && S.view === "settings" && S.ui.tab === "audio") renderSettingsPage();
      if (d.error && d.error !== before.error && !d.on) toast(t("n_source_error", { name: t("src_" + d.key), error: d.error }), 7000, "error");
    },
    message: (m) => {
      const i = S.messages.findIndex((x) => x.id === m.id);
      if (i >= 0) S.messages[i] = m; else S.messages.push(m);
      if (S.ui.tryId === "next" && m.via === "typed") S.ui.tryId = m.id;
      upsertMessage(m);
      if (S.messages.length === 1) renderSources();
      if (S.view === "wizard" && S.ui.wz === 3 && m.id === S.ui.tryId) {
        const slot = document.querySelector(".wz-result");
        if (slot) slot.innerHTML = `<div class="msg me" style="max-width:100%;align-items:flex-start"><div class="bubble">${bubbleHTML(m).replace(/<div class="actions">.*?<\/div>/, "")}</div></div>`;
      }
    },
    partial: (d) => {      // 譯文邊翻邊來：只換那一行字，不重畫整則
      const m = S.messages.find((x) => x.id === d.id);
      if (!m || !m.pending) return;
      m.partial = d.text;
      const el = $("part-" + d.id);
      if (el) { el.textContent = d.text; el.className = "text streaming"; }
    },
    message_removed: (d) => {
      S.messages = S.messages.filter((x) => x.id !== d.id);
      const el = $("m-" + d.id); if (el) el.remove();
      if (!S.messages.length) renderMessages();
    },
    record: (d) => {
      const was = S.record.on;
      S.record = d;
      if (was !== d.on) S.ui.recHidden = false;
      renderRecord();
      if (was !== d.on && S.view === "settings" && S.ui.tab === "general") renderSettingsPage();
    },
    cleared: () => { S.messages = []; renderMessages(); renderSources(); },
    config: (cfg) => {
      const before = S.cfg;
      S.cfg = cfg;
      if (before.theme !== cfg.theme || before.accent !== cfg.accent) applyTheme();
      if (S.ui.help && (before.ui_lang !== cfg.ui_lang || before.ptt_key !== cfg.ptt_key || JSON.stringify(before.hotkeys) !== JSON.stringify(cfg.hotkeys))) {
        S.ui.help = null;      // 說明裡寫著快速鍵，而且是照介面語言給的：改了就要重拿
        if (S.view === "settings" && S.ui.tab === "help") loadHelp();
      }
      if (before.ui_lang !== cfg.ui_lang) { renderAll(); return; }
      renderLangs(); renderSources(); renderOverlayState();
      if (before.show_pronunciation !== cfg.show_pronunciation || before.back_translate !== cfg.back_translate) renderMessages();
      if (S.view === "settings" && S.ui.tab === "overlay"
          && Object.keys(cfg).some((k) => (k.startsWith("overlay") || k.endsWith("_lang")) && JSON.stringify(before[k]) !== JSON.stringify(cfg[k]))) refreshPreview();
      if (S.view === "settings" && !S.ui.live && !S.ui.mapDrag) renderSettingsPage();
      if (S.view === "settings" && S.ui.tab === "overlay" && ["overlay", "overlay_form", "overlay_monitor", "overlay_font"].some((k) => before[k] !== cfg[k])) loadScreens();
      if (S.view === "wizard" && (S.ui.wz !== 3)) renderWizard();
    },
    glossary: (items) => { S.glossary = items; if (S.view === "settings" && S.ui.tab === "quality") renderSettingsPage(); },
    speak: (sp) => { S.speak = sp; if (S.view === "settings" && S.ui.tab === "audio") renderSettingsPage(); },
    replies: (r) => { S.replies = r; S.ui.replyCopied = null; renderMessages(); },
    words: (d) => { S.words[d.id] = d.items; const m = S.messages.find((x) => x.id === d.id); if (m) upsertMessage(m); },
    phrases: (st) => { S.phrases = st; if (S.view === "settings" && S.ui.tab === "general" && !(document.activeElement && document.activeElement.dataset && document.activeElement.dataset.phraseEdit)) { const sec = $("phrase-section"); if (sec) sec.innerHTML = `<div><h2>${esc(t("ph_title"))}</h2><div class="sub">${esc(t("ph_hint"))}</div></div>${phrasebookHTML()}`; } },
    wordbook: (st) => { S.wordbook = st; if (S.view === "settings" && S.ui.tab === "learn") loadWords(); },
    recaps: (st) => { S.recaps = st; if (S.view === "settings" && S.ui.tab === "history") { paintHistory(); if (st.state !== "thinking") loadRecaps(); } },
    stream: (st) => { S.stream = st; if (S.view === "settings" && S.ui.tab === "overlay") renderSettingsPage(); },
    game: (g) => { S.game = g; if (S.view === "settings" && S.ui.tab === "quality") renderSettingsPage(); },
    lessons: (items) => { S.lessons = items; if (S.view === "settings" && S.ui.tab === "quality") renderSettingsPage(); },
    memory: (m) => { S.memory = m; if (S.view === "settings" && S.ui.tab === "quality" && !document.activeElement.closest("form")) renderSettingsPage(); },
    levels: (levels) => { S.ui.levels = levels; paintLevels(); },
    speakers: (d) => {      // 分辨說話者：模型的狀態、多了一個人、有人改了名字
      const before = S.speakers || {};
      S.speakers = d;
      const percent = $("spk-percent");
      if (percent && d.state === "downloading" && before.state === "downloading") { percent.textContent = t("spk_downloading", { mb: d.mb, percent: d.percent || 0 }); return; }
      const at = (document.activeElement && document.activeElement.dataset) || {};
      const typing = at.speaker || at.spkLang;      // 正在打名字、正在選語言：不要重畫把字洗掉、把選單關掉
      if (S.view === "settings" && S.ui.tab === "audio" && !S.ui.live && !typing) renderSettingsPage();
      const sign = (x) => JSON.stringify([x.on, x.run, (x.people || []).map((p) => [p.id, p.name])]);
      if (sign(before) !== sign(d)) renderMessages();
    },
    speech_gpu: (d) => {
      const before = S.speechGpu || {};
      S.speechGpu = d;
      const percent = $("sgpu-percent");
      if (percent && d.state === "downloading" && before.state === "downloading") percent.textContent = t("sgpu_downloading", { percent: d.percent || 0 });
      else if (S.view === "settings" && S.ui.tab === "audio" && !S.ui.live) renderSettingsPage();
    },
    delay: (d) => { S.delay = d; const note = $("delay-note"); if (note) note.querySelector("span").textContent = delayText(); },
    vu: (d) => { S.vu = d; paintVu(); },
    find_mic: (d) => {
      if (d.state === "cancelled") S.ui.find = null;
      else if (d.state === "found" && !d.previous) {
        S.ui.find = null;
        toast(t(d.changed ? "find_found" : "find_same", { name: d.name }), 6000);
      } else S.ui.find = d;      // 換了麥克風：留在畫面上，可以按「換回去」
      renderMicViews();
    },
    notice: (d) => toast(t("n_" + d.code, d.lang ? { ...d, lang: langName(d.lang) } : d), 7000, /failed|small/.test(d.code) ? "warn" : "info"),
    overlay: (d) => {
      const before = S.overlay || {};
      S.overlay = d;
      renderOverlayState();
      if (d.rect && S.ui.screens && !S.ui.mapDrag) { S.ui.screens.rect = d.rect; paintMapBox(); }      // 在真的螢幕上拖：螢幕圖跟著動
      if (S.view === "settings" && S.ui.tab === "overlay" && (before.editing !== d.editing || before.running !== d.running)) {
        renderSettingsPage();
        if (before.running !== d.running) loadScreens();
      }
    },
    history: (d) => {      // 有新的句子記下來了（或清掉了）：正在看歷史頁就更新，等一下再查，免得連續幾句一直閃
      S.history = d;
      if (S.view !== "settings" || S.ui.tab !== "history") return;
      clearTimeout(histTimer);
      histTimer = setTimeout(() => loadHistory(), 600);
    },
    update_offer: (d) => {      // 程式開著的時候查到新版本（空的 = 提醒收起來了）
      S.updateOffer = d && d.version ? d : null;
      if (!S.updateOffer) S.ui.upReview = false;
      renderUpdateBanner(); renderOverlay();
    },
    storage: (d) => { S.ui.storage = d; if (S.view === "settings" && S.ui.tab === "storage") renderSettingsPage(); },
    storage_move: (d) => {      // 搬的進度：只改數字和進度條，不重畫整頁
      if (S.ui.storage) S.ui.storage.moving = d;
      const text = $("store-moving-text"), bar = $("store-moving-bar");
      if (text) text.textContent = t("sto_moving", { folder: d.to, done: sizeText(d.done), total: d.total ? sizeText(d.total) : "…" });
      if (bar) bar.style.width = (d.total ? Math.round(100 * d.done / d.total) : 0) + "%";
    },
    hotkeys: (d) => { healthSoon(); S.hotkeys = d; if (S.view === "settings" && S.ui.tab === "general") renderSettingsPage(); },
    saved: (d) => flashSaved(d.what === "region" && d.id ? "#region-" + d.id : d.what),
    screen_stats: (d) => {      // 讀螢幕的統計：直接換那一行字，不重畫整頁（免得正在打的名字被打斷）
      S.screenStats = d;
      Object.keys(d).forEach((id) => { const el = $("rstat-" + id); if (el) el.textContent = regionStats(id); });
    },
    view: (d) => { if (d.view !== S.view) showView(d.view); },
    region_done: () => {
      if (S.view === "wizard") renderWizard();
      if (S.view === "settings") renderSettingsPage();
    }
  };

  function connect() {
    const es = new EventSource("/api/events?t=" + encodeURIComponent(TOKEN));
    let opened = false;
    es.onopen = () => { if (opened) load(); opened = true; };
    Object.keys(handlers).forEach((name) => {
      es.addEventListener(name, (e) => { try { handlers[name](JSON.parse(e.data)); } catch (err) { console.error(name, err); } });
    });
  }

  async function load() {
    const st = await get("/api/state");
    if (!st || st.error) { setTimeout(load, 1000); return false; }
    S.cfg = st.config; S.phase = st.phase; S.detail = st.detail || {}; S.gpu = st.gpu; S.mode = st.mode;
    S.fallback = st.fallback; S.sources = st.sources; S.messages = st.messages; S.glossary = st.glossary; S.game = st.game || {}; S.stream = st.stream || {}; S.speak = st.speak || {}; S.replies = st.replies || {}; S.words = st.words || {}; S.langs = st.langs || {}; S.wordbook = st.wordbook || S.wordbook; S.phrases = st.phrases || S.phrases; S.recaps = st.recaps || S.recaps; S.lessons = st.lessons || []; S.memory = st.memory || S.memory;
    S.speakers = st.speakers || {}; S.speechGpu = st.speech_gpu || {}; S.delay = st.delay || null; S.pttKeys = st.ptt_keys || []; S.screenStats = st.screen_stats || {};
    S.changes = st.changes || {};
    S.whatsNew = st.whats_new || [];
    S.update = st.update || {};
    S.updateOffer = st.update_offer && st.update_offer.version ? st.update_offer : null;
    if (st.updated_from && !S.whatsNew.length) {      // 剛自動更新（或退回）完第一次開：說一次
      toast(t(S.update.skipped === st.updated_from ? "up_back_done" : "up_done", { version: st.version }), 12000);
      post("/api/seen", { update: true });
    }
    S.version = st.version; S.canWindow = st.can_window; S.overlay = st.overlay || S.overlay; S.record = st.record || S.record;
    S.hotkeys = st.hotkeys || S.hotkeys; S.history = st.history || S.history; S.settingsBackup = st.settings_backup || null;
    const view = S.cfg.setup_done ? (st.view === "wizard" ? "main" : st.view) : "wizard";
    app.dataset.view = S.view = view;
    renderAll();
    const splash = $("splash");
    if (splash) { splash.classList.add("gone"); setTimeout(() => splash.remove(), 400); }
    if (view !== st.view) post("/api/view", { view });
    if (view === "settings") { renderSettings(); tabOpened(); }
    return true;
  }

  load().then(connect);
})();
