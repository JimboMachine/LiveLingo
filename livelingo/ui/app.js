// LiveLingo 的介面。所有工作都在背景的程式做，這裡只負責顯示和把按鈕的動作送過去。
(function () {
  "use strict";

  const TOKEN = new URLSearchParams(location.search).get("t") || "";
  const $ = (id) => document.getElementById(id);
  const app = $("app");

  const S = {
    cfg: null, phase: "starting", detail: {}, gpu: {}, mode: {}, fallback: false, version: "",
    sources: {}, messages: [], glossary: [], lessons: [], canWindow: false, view: "main", overlay: { editing: false, running: false }, vu: {},
    record: { on: false, since: 0, count: 0, file: "", name: "", folder: "", saved: 0 },
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
  const PAIR_LANGS = ["zh-TW", "zh-CN", "ko", "en", "ja"];      // 可以選的翻譯語言
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
    ui_lang: '[data-act="ui-lang"][aria-pressed="true"]', tone: '[data-act="tone"][aria-pressed="true"]',
    quality: '[data-act="quality"][aria-pressed="true"]', my_lang: "#sel-my", their_lang: "#sel-their", their_auto: "#sel-their",
    speaker_device: '[data-act="device"][data-key="speaker"]', mic_device: '[data-act="device"][data-key="mic"]',
    transcript_dir: '[data-act="rec-choose"]', region: '[data-act="region"]', overlay_rect: '[data-act="overlay-edit"]',
    overlay_look: '[data-act="overlay-look-reset"]'
  };
  let savedTimer = null;
  function placeSaved() {      // 設定頁重畫之後也會再叫一次，回饋不會因為重畫就不見
    const chip = $("saved-chip"), mark = S.ui.saved;
    document.querySelectorAll(".just-saved").forEach((el) => el.classList.remove("just-saved"));
    if (!mark || Date.now() > mark.until) { chip.hidden = true; return; }
    const page = $("settings-page");
    const el = S.view === "settings" && (page.querySelector(mark.key[0] === "#" ? mark.key : mark.key.startsWith("preset-") ? `[data-act="overlay-preset"][data-code="${mark.key.slice(7)}"]` : SAVE_TARGET[mark.key] || `[data-key="${mark.key}"][aria-pressed="true"]`)
      || page.querySelector(`[data-key="${mark.key}"], [data-change="${mark.key}"]`));
    const row = el && (el.closest(".setting-row, .range-row, .field") || el.closest(".btn-row, .pills, .swatches"));
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
    el.dataset.level = level || "info";
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
    back: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
    x: '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    frame: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" style="stroke-width:2.2"><path d="M4 9V5h4M20 9V5h-4M4 15v4h4M20 15v4h-4"/></svg>',
    chat: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 7h3M10 7h10M4 12h3M10 12h7M4 17h3M10 17h9"/></svg>',
    swap: '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path d="M7 7h13l-3-3M17 17H4l3 3"/></svg>',
    bubble: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-8l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/></svg>',
    shield: '<svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  };
  const MARK = window.BRAND.mark;      // 品牌標誌和名字都在 brand.js
  const WORDMARK = `${window.BRAND.name[0]}<b>${window.BRAND.name[1]}</b>`;

  // ---------------------------------------------------------------- 固定文字

  function applyI18n() {
    const htmlLang = { "zh-TW": "zh-Hant", "zh-CN": "zh-Hans", en: "en", ko: "ko", ja: "ja" }[S.cfg.ui_lang] || "en";
    document.documentElement.lang = htmlLang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-label]").forEach((el) => {
      el.setAttribute("aria-label", t(el.dataset.i18nLabel));
      el.title = t(el.dataset.i18nLabel);
    });
  }

  // ---------------------------------------------------------------- 主題

  const ACCENTS = { mint: ["#4ADE9A", "#0B8A5C"], blue: ["#6CB6FF", "#1D6FD1"], violet: ["#B7A1FF", "#6B4FD8"], rose: ["#FF8FA8", "#C8325A"], cyan: ["#4DD8E6", "#0B7F8C"] };
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
    if (s.busy) return t("src_busy");
    if (s.error && !s.on) return t("src_error");
    if (key === "screen") return s.on ? t("screen_on") : t("screen_off");
    const device = s.device || (key === "listen" ? S.cfg.speaker_name : S.cfg.mic_name) || t("default_device");
    if (key === "listen") return s.on ? (s.speaking ? t("listen_speaking") : t("listen_on", { device })) : t("listen_off");
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
    const r = S.cfg.region;
    const showRegion = !!(r && (S.sources.screen || {}).on);
    row.hidden = !(showRegion || S.messages.length);
    row.innerHTML = (showRegion
      ? `<span>${esc(t("region_now", { w: r.width, h: r.height }))} · <button data-act="region">${esc(t("region_change"))}</button></span>`
      : "<span></span>")
      + (S.messages.length ? `<button data-act="clear">${esc(t("clear"))}</button>` : "");
    const mic = $("sub-mic");
    mic.classList.toggle("on", !!(S.sources.mic || {}).on);
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
    let body = `<div class="orig">${esc(m.original)}</div>`;
    // 辨識的時候沒什麼把握：標出來，按下去可以把原文改對再重翻
    const unsure = m.unsure && !m.pending
      ? `<button class="unsure" data-act="fix" data-id="${m.id}">${ICON.info}<span>${esc(t(m.via === "screen" ? "unsure_seen" : "unsure_heard"))}</span></button>` : "";
    if (m.error) {
      body += `<div class="text failed">${esc(t("failed", { error: m.error }))}</div>`;
    } else if (m.pending) {
      body += `<div class="text pending">${esc(t("translating"))}</div>`;
    } else {
      if (m.translated === m.original) body = "";      // 兩邊同一種語言時原文和譯文一樣，顯示一次就好
      body += unsure + `<div class="text">${esc(m.translated)}</div>`;
      if (m.back && S.cfg.back_translate) body += `<div class="back">${ICON.back}<span>${esc(t("back", { text: m.back }))}</span></div>`;
      if (m.pron && S.cfg.show_pronunciation) body += `<div class="pron">${esc(t("pron", { text: m.pron }))}</div>`;
      if (m.copied) body += `<div class="copied">${ICON.check}<span>${esc(t("copied_hint"))}</span></div>`;
      body += `<div class="actions"><button data-act="fix" data-id="${m.id}">${esc(t("wrong"))}</button>` +
        `<button data-act="copy" data-id="${m.id}">${esc(t("copy"))}</button></div>`;
    }
    return body;
  }

  function metaHTML(m) {
    const icon = m.via === "screen" ? ICON.screen : (m.via === "heard" ? ICON.listen : (m.via === "spoken" ? ICON.mic : ""));
    return `${icon}<span>${esc(t("who_" + m.who))} · ${esc(t("via_" + m.via))}</span>`;
  }

  function fixHTML(m) {
    const words = [];
    m.original.split(/[\s,.!?~…·、，。！？「」『』()（）\[\]<>"']+/).forEach((w) => {
      if (w && words.indexOf(w) < 0 && words.length < 16) words.push(w);
    });
    // 語音和螢幕上讀來的句子：可能是原文就辨識錯了，先讓他改原文
    const edit = m.via === "typed" ? "" : `
      <label class="dim small" for="edit-${m.id}">${esc(t(m.via === "screen" ? "edit_label_seen" : "edit_label_heard"))}</label>
      <textarea id="edit-${m.id}" class="edit-src" rows="2" maxlength="500">${esc(m.original)}</textarea>
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

  function renderSubtitle() {
    const last = S.messages[S.messages.length - 1];
    const badge = $("sub-badge"), main = $("sub-main"), orig = $("sub-orig");
    if (!last) {
      badge.textContent = ""; orig.textContent = "";
      main.textContent = t("subtitle_idle"); main.className = "sub-main idle";
      return;
    }
    badge.textContent = t("who_" + last.who);
    badge.className = "badge" + (last.who === "me" ? " me" : "");
    main.className = "sub-main";
    main.textContent = last.pending || last.error ? last.original : last.translated;
    orig.textContent = last.pending || last.error ? "" : last.original;
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
    post("/api/meters", { on: true });
  }

  function refreshDevicePanels() {
    if (S.view === "settings" && S.ui.tab === "audio") renderSettingsPage();
    if (S.view === "wizard" && S.ui.wz === 1) renderWizard();
  }

  // ---------------------------------------------------------------- 設定

  const TABS = [["overlay", "nav_overlay", "chat"], ["audio", "nav_audio", "listen"], ["quality", "nav_quality", "quality"], ["screen", "nav_screen", "screen"], ["general", "nav_general", "globe"], ["about", "nav_about", "info"]];

  // 固定在左邊的導覽：對話和每一頁設定都在同一層，視窗不用為了設定變大變小
  function renderRail() {
    const item = (to, key, icon) => {
      const here = to === "main" ? S.view === "main" : (S.view === "settings" && S.ui.tab === to);
      return `<button class="rail-item" data-act="nav" data-to="${to}" ${here ? 'aria-current="page"' : ""} title="${esc(t(key))}">${ICON[icon]}<span>${esc(t(key))}</span></button>`;
    };
    $("rail").innerHTML = `
      <div class="rail-brand">${MARK}<div class="wordmark">${WORDMARK}</div></div>
      ${item("main", "nav_chat", "bubble")}
      <div class="rail-title">${esc(t("settings"))}</div>
      ${TABS.filter(([id]) => id !== "about").map(([id, key, icon]) => item(id, key, icon)).join("")}
      <div class="grow"></div>
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
    const rows = S.glossary.length
      ? S.glossary.map((g) => `<div class="grow-row"><span>${esc(g.src)}</span><span>${esc(g.dst)}</span>
          <button data-act="gloss-remove" data-src="${esc(g.src)}" aria-label="${esc(t("remove", { word: g.src }))}">${ICON.x}</button></div>`).join("")
      : `<div class="none">${esc(t("gloss_empty"))}</div>`;
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
      </section>
      <section>
        <h2>${esc(tk("tone_title"))}</h2>
        <div class="pills">
          <button class="pill tall" data-act="tone" data-tone="casual" aria-pressed="${S.cfg.tone === "casual"}">${esc(tk("tone_casual"))}</button>
          <button class="pill tall" data-act="tone" data-tone="polite" aria-pressed="${S.cfg.tone === "polite"}">${esc(tk("tone_polite"))}</button>
        </div>
      </section>
      <section>${switchRow("use_context", t("use_context"), t("use_context_desc"))}
        ${switchRow("back_translate", t("back_translate"), t("back_translate_desc"))}</section>
      <section>${switchRow("game_terms", t("terms_title"), t("terms_desc"))}</section>
      <section>
        <div><h2>${esc(t("gloss_title"))}</h2><div class="sub">${esc(t("gloss_hint"))}</div></div>
        <form class="gloss-form" id="gloss-form">
          <input name="src" aria-label="${esc(t("gloss_src"))}" placeholder="${esc(t("gloss_src"))}" maxlength="80">
          <input name="dst" aria-label="${esc(t("gloss_dst"))}" placeholder="${esc(t("gloss_dst"))}" maxlength="80">
          <button class="primary" type="submit">${esc(t("gloss_add"))}</button>
        </form>
        <div class="gloss"><div class="grow-head"><span>${esc(t("gloss_src"))}</span><span>${esc(t("gloss_dst"))}</span><span></span></div>${rows}</div>
      </section>
      <section>
        <div><h2>${esc(t("lessons_title"))}</h2><div class="sub">${esc(t("lessons_hint"))}</div></div>
        <div class="gloss"><div class="grow-head"><span>${esc(t("lessons_src"))}</span><span>${esc(t("lessons_dst"))}</span><span></span></div>${taught}</div>
      </section>`;
  }

  function screenPage() {
    const r = S.cfg.region;
    const o = S.ui.ocr;
    let result = "";
    if (o === "reading") result = `<div class="note">${ICON.info}<span>${esc(t("ocr_reading"))}</span></div>`;
    else if (o && !o.ok) result = `<div class="note warn">${ICON.info}<span>${esc(t("ocr_failed", { error: o.error }))}</span></div>`;
    else if (o && !o.lines.length) result = `<div class="note">${ICON.info}<span>${esc(t("ocr_nothing"))}</span></div>`;
    else if (o) result = `<div class="ocr-lines">${o.lines.map((l) => `<div>${esc(l)}</div>`).join("")}</div>`;
    return `
      <section>
        <div><h2>${esc(t("screen_title"))}</h2><div class="sub">${esc(t("screen_hint"))}</div></div>
        <div class="note">${ICON.info}<span>${esc(r ? t("region_size", { w: r.width, h: r.height, x: r.left, y: r.top }) : t("region_none"))}</span></div>
        <div class="btn-row">
          <button class="primary big" data-act="region">${ICON.frame}${esc(r ? t("region_change") : t("region_pick"))}</button>
          <button class="ghost" data-act="ocr-test" ${r ? "" : "disabled"}>${esc(t("ocr_test"))}</button>
        </div>
        <div class="sub">${esc(t("ocr_test_hint"))}</div>
        ${result}
      </section>
      <section>${switchRow("also_english", t("also_english"))}</section>`;
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
    overlay_lines: (v) => (v > 0 ? t("ov_lines_value", { n: v }) : t("ov_lines_all"))
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

  function overlayPage() {
    const on = !!S.cfg.overlay;
    const editing = !!S.overlay.editing;
    return `
      <section>
        <div><h2>${esc(t("ov_title"))}</h2><div class="sub">${esc(t("ov_hint"))}</div></div>
        ${switchRow("overlay", t("ov_enable"))}
        <div class="btn-row">
          <button class="primary big" data-act="overlay-edit" ${on ? "" : "disabled"}>${ICON.frame}${esc(editing ? t("ov_done") : t("ov_move"))}</button>
          <button class="ghost" data-act="overlay-test" ${on ? "" : "disabled"}>${esc(t("ov_test"))}</button>
          <button class="ghost" data-act="overlay-reset" ${on ? "" : "disabled"}>${esc(t("ov_reset"))}</button>
        </div>
        <div class="note">${ICON.info}<span>${esc(t("ov_game_note"))}</span></div>
      </section>
      <section><div><h2>${esc(t("ov_presets"))}</h2><div class="sub">${esc(t("ov_presets_hint"))}</div></div>
        <div class="quality presets">${["chat", "subtitle", "quiet"].map((id) => `<button class="qcard" data-act="overlay-preset" data-code="${id}">
          <span class="head"><span class="name">${esc(t("ov_preset_" + id))}</span></span><span class="desc">${esc(t("ov_preset_" + id + "_desc"))}</span></button>`).join("")}</div>
      </section>
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
        ${choiceRow("overlay_align", t("ov_align"), [["left", t("ov_align_left")], ["center", t("ov_align_center")], ["right", t("ov_align_right")]])}
        ${choiceRow("overlay_grow", t("ov_grow"), [["up", t("ov_grow_up")], ["down", t("ov_grow_down")]])}
        ${rangeRow("overlay_lines", t("ov_lines"), 0, 12, 1, RANGE_TEXT.overlay_lines)}
        ${rangeRow("overlay_seconds", t("ov_seconds"), 0, 60, 1, RANGE_TEXT.overlay_seconds)}
        ${switchRow("overlay_original", t("ov_original"))}
        ${switchRow("overlay_mine", t("ov_mine"))}
      </section>
      <section><div class="btn-row"><button class="ghost" data-act="overlay-look-reset">${esc(t("ov_look_reset"))}</button></div></section>`;
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
    const natives = [["zh-TW", "繁體中文"], ["zh-CN", "简体中文"], ["en", "English"], ["ko", "한국어"], ["ja", "日本語"]];
    return `<div class="pills ui-langs">${natives.map(([id, native]) => {      // 一排小按鈕：語言再多也只是多一顆，不會把版面擠開
      const on = S.cfg.ui_lang === id;
      return `<button class="pill tall" data-act="ui-lang" data-code="${id}" aria-pressed="${on}" title="${esc(on ? t("using") : t("click_switch"))}">${native}</button>`;
    }).join("")}</div>`;
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
      <section><div class="btn-row"><button class="ghost" data-act="rerun-setup">${esc(t("rerun_setup"))}</button></div></section>`;
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
        ${fact("info", t("about_update_title"), t("about_update"))}
      </section>`;
  }

  function renderSettingsPage() {
    const page = $("settings-page");
    const scroll = page.scrollTop;
    const active = page.contains(document.activeElement) ? document.activeElement : null;
    const focus = !active ? "" : active.id ? "#" + active.id : active.dataset.act
      ? `[data-act="${active.dataset.act}"]` + ["key", "value", "code", "q", "tone", "id"].filter((k) => active.dataset[k] != null).map((k) => `[data-${k}="${CSS.escape(active.dataset[k])}"]`).join("") : "";
    const typed = [...page.querySelectorAll("input[id]:not([type=range]), textarea[id]")].map((el) => [el.id, el.value]);
    if (S.ui.tab === "audio") page.innerHTML = devicesHTML();
    else if (S.ui.tab === "quality") page.innerHTML = qualityPage();
    else if (S.ui.tab === "screen") page.innerHTML = screenPage();
    else if (S.ui.tab === "overlay") page.innerHTML = overlayPage();
    else if (S.ui.tab === "about") page.innerHTML = aboutPage();
    else page.innerHTML = generalPage();
    if (S.ui.tab !== "about") page.insertAdjacentHTML("beforeend", `<div class="autosave">${ICON.check}<span>${esc(t("autosave"))}</span></div>`);
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
    const page = $("settings-page");
    page.scrollTop = 0;
    page.classList.remove("in"); void page.offsetWidth; page.classList.add("in");      // 換頁的淡入
    if (tab === "audio") openDevices();
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
      const r = S.cfg.region;
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
    renderWizard();
    $("wizard").scrollTop = 0;
    if (S.ui.wz === 1) openDevices();
    else if (was === 1) post("/api/meters", { on: false });
  }

  // ---------------------------------------------------------------- 蓋板

  function renderOverlay() {
    const el = $("overlay");
    const d = S.detail || {};
    let html = "";
    if (S.phase === "updating") {
      const text = d.step === "downloading" ? t("up_downloading", { version: d.version, percent: d.percent || 0 })
        : (d.step === "installing" ? t("up_installing", { version: d.version }) : t("up_checking"));
      if (d.step === "ask") {
        html = `<h2>${esc(t("up_ask_title", { version: d.version }))}</h2><p>${esc(t("up_ask_text"))}</p>
          <div class="btn-row center"><button class="primary" data-act="update-now">${esc(t("up_now"))}</button>
          <button class="ghost" data-act="update-later">${esc(t("up_later"))}</button></div>`;
      } else {
        html = `<div class="spinner"></div><h2>${esc(t("st_updating"))}</h2><p>${esc(text)}</p>` +
          (d.step === "downloading" ? `<div class="bar"><span style="width:${d.percent || 0}%"></span></div>
            <div class="btn-row center"><button class="ghost" data-act="update-cancel">${esc(t("cancel"))}</button></div>` : "");
      }
    } else if (S.phase === "error") {
      html = `<h2>${esc(t("err_title"))}</h2><p>${esc(d.message || "")}</p>` +
        (d.log ? `<p>${esc(t("err_log", { path: d.log }))}</p>` : "") +
        `<button class="primary" data-act="retry">${esc(t("retry"))}</button>`;
    } else if (S.view !== "wizard" && S.phase === "need_download") {
      html = `<h2>${esc(t("st_need_download"))}</h2><p>${esc(d.error ? t("dl_error", { error: d.error }) : t("dl_need", { gb: gb(d.total_mb) }))}</p>
        <button class="primary" data-act="download">${esc(d.error ? t("retry") : t("dl_start"))}</button>` +
        (S.cfg.quality !== "standard" && S.mode.quality === "high" ? `<button class="link" data-act="quality" data-q="standard">${esc(t("q_standard"))}</button>` : "");
    } else if (S.view !== "wizard" && S.phase === "downloading") {
      html = `<h2>${esc(t("dl_title"))}</h2><p>${esc(downloadLine())}</p><div class="bar"><span style="width:${percent()}%"></span></div>`;
    }
    el.hidden = !html;
    el.innerHTML = html ? `<div class="panel">${html}</div>` : "";
  }

  // ---------------------------------------------------------------- 畫面切換

  function showView(view) {
    S.view = view;
    app.dataset.view = view;
    renderRail();
    if (view === "settings") { renderSettingsPage(); if (S.ui.tab === "audio") openDevices(); }
    if (view === "wizard") renderWizard();
    if (view === "subtitle") renderSubtitle();
    renderOverlay();
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
    renderSubtitle();
    renderRail();
    if (S.view === "settings") renderSettingsPage();
    if (S.view === "wizard") renderWizard();
    renderOverlay();
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
    region: () => post("/api/region", { enable: true }),
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
    toggle: (el) => saveCfg({ [el.dataset.key]: !S.cfg[el.dataset.key] }),
    set: (el) => saveCfg({ [el.dataset.key]: el.dataset.value }),
    "overlay-preset": async (el) => { const r = await post("/api/overlay", { preset: el.dataset.code }); if (r.ok && r.saved) flashSaved("preset-" + el.dataset.code); },
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
    "update-now": () => post("/api/update", { choice: "now" }),
    "update-later": () => post("/api/update", { choice: "later" }),
    "update-cancel": () => post("/api/update", { choice: "cancel" }),
    "find-cancel": () => post("/api/find_mic", { cancel: true }),
    "find-undo": () => {
      const prev = S.ui.find && S.ui.find.previous;
      S.ui.find = null;
      renderMicViews();
      if (prev) post("/api/config", { mic_device: prev.id, mic_name: prev.name });
    },
    "mic-settings": () => post("/api/mic_settings"),
    "gloss-remove": (el) => post("/api/glossary/remove", { src: el.dataset.src }),
    "lesson-remove": (el) => post("/api/lessons/remove", { src: el.dataset.src, target: el.dataset.target }),
    "ocr-test": async () => {
      S.ui.ocr = "reading"; renderSettingsPage();
      S.ui.ocr = await post("/api/ocr_test"); renderSettingsPage();
    },
    "ui-lang": (el) => saveCfg({ ui_lang: el.dataset.code }),
    theme: (el) => saveCfg({ theme: el.dataset.code }),
    accent: (el) => saveCfg({ accent: el.dataset.code }),
    swap: () => saveCfg({ my_lang: S.cfg.their_lang, their_lang: S.cfg.my_lang, their_auto: false }),
    "rerun-setup": () => { S.ui.wz = 0; setView("wizard"); },
    "wz-begin": () => { if (S.phase === "need_download") post("/api/download"); wizardGo(1); },
    "wz-next": () => wizardGo(S.ui.wz + 1),
    "wz-prev": () => wizardGo(S.ui.wz - 1),
    "wz-region": () => post("/api/region", { enable: false }),
    "wz-finish": async () => {
      await post("/api/config", { setup_done: true });
      setView("main");
      if (S.cfg.region) post("/api/source", { key: "screen", on: true });
    },
    "overlay-toggle": async () => {
      const on = !S.cfg.overlay;
      await post("/api/config", { overlay: on });
      if (on) { toast(t("n_overlay_on"), 5000); post("/api/overlay", { test: true }); }
    },
    "overlay-edit": () => post("/api/overlay", { edit: !S.overlay.editing }),
    "overlay-test": () => post("/api/overlay", { test: true }),
    "overlay-reset": () => post("/api/overlay", { reset: true }),
    download: () => post("/api/download"),
    retry: () => post("/api/retry")
  };

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const fn = actions[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el); }
  });

  let liveTimer = null, livePatch = null;
  function patchOf(el) {      // 一個控制項現在的值，寫成要送去存的樣子
    const value = el.type === "range" ? Number(el.value) : el.value;
    return { [el.dataset.change]: el.dataset.sub ? { [el.dataset.sub]: value } : value };
  }
  document.addEventListener("input", (e) => {
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
    const key = e.target.dataset && e.target.dataset.change;
    if (!key) return;
    if (e.target.dataset.range || e.target.dataset.live) {      // 放開滑桿、選好顏色：這才算存好
      clearTimeout(liveTimer); liveTimer = null; livePatch = null; S.ui.live = null;
      saveCfg(patchOf(e.target), e.target.id ? "#" + e.target.id : key);
    } else if (key === "their_lang") saveCfg(e.target.value === "auto" ? { their_auto: true } : { their_lang: e.target.value, their_auto: false }, key);
    else saveCfg({ [key]: e.target.value }, key);
  });
  document.addEventListener("pointerup", () => { if (S.ui.live) setTimeout(() => { S.ui.live = null; }, 300); });

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
    } else if (form.id === "gloss-form") {
      const src = form.elements.src.value.trim(), dst = form.elements.dst.value.trim();
      if (!src || !dst) { (src ? form.elements.dst : form.elements.src).focus(); return; }
      const r = await post("/api/glossary/add", { src, dst });
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
    if (S.ui.langOpen) { S.ui.langOpen = false; renderLangs(); }
    document.querySelectorAll(".fixslot").forEach((s) => { s.innerHTML = ""; });
  });

  // ---------------------------------------------------------------- 背景送來的事件

  const handlers = {
    phase: (d) => {
      const before = S.phase;
      S.phase = d.phase; S.detail = d.detail || {};
      if (d.mode) S.mode = d.mode;
      if (d.gpu) S.gpu = d.gpu;
      if (typeof d.fallback === "boolean") S.fallback = d.fallback;
      renderStatus(); renderOverlay();
      if (S.view === "wizard") { if (before !== S.phase) renderWizard(); else renderWizardStrip(); }
      if (S.view === "settings" && S.ui.tab === "quality" && before !== S.phase) renderSettingsPage();
    },
    source: (d) => {
      const before = S.sources[d.key] || {};
      S.sources[d.key] = d;
      renderSources();
      if (d.key === "mic" && (d.quiet || "") !== (before.quiet || "") && S.view === "settings" && S.ui.tab === "audio") renderSettingsPage();
      if (d.error && d.error !== before.error && !d.on) toast(t("n_source_error", { name: t("src_" + d.key), error: d.error }), 7000, "error");
    },
    message: (m) => {
      const i = S.messages.findIndex((x) => x.id === m.id);
      if (i >= 0) S.messages[i] = m; else S.messages.push(m);
      if (S.ui.tryId === "next" && m.via === "typed") S.ui.tryId = m.id;
      upsertMessage(m);
      if (S.messages.length === 1) renderSources();
      renderSubtitle();
      if (S.view === "wizard" && S.ui.wz === 3 && m.id === S.ui.tryId) {
        const slot = document.querySelector(".wz-result");
        if (slot) slot.innerHTML = `<div class="msg me" style="max-width:100%;align-items:flex-start"><div class="bubble">${bubbleHTML(m).replace(/<div class="actions">.*?<\/div>/, "")}</div></div>`;
      }
    },
    message_removed: (d) => {
      S.messages = S.messages.filter((x) => x.id !== d.id);
      const el = $("m-" + d.id); if (el) el.remove();
      if (!S.messages.length) renderMessages();
      renderSubtitle();
    },
    record: (d) => {
      const was = S.record.on;
      S.record = d;
      if (was !== d.on) S.ui.recHidden = false;
      renderRecord();
      if (was !== d.on && S.view === "settings" && S.ui.tab === "general") renderSettingsPage();
    },
    cleared: () => { S.messages = []; renderMessages(); renderSources(); renderSubtitle(); },
    config: (cfg) => {
      const before = S.cfg;
      S.cfg = cfg;
      if (before.theme !== cfg.theme || before.accent !== cfg.accent) applyTheme();
      if (before.ui_lang !== cfg.ui_lang) { renderAll(); return; }
      renderLangs(); renderSources(); renderOverlayState();
      if (before.show_pronunciation !== cfg.show_pronunciation || before.back_translate !== cfg.back_translate) renderMessages();
      if (S.view === "settings" && S.ui.tab === "overlay"
          && Object.keys(cfg).some((k) => (k.startsWith("overlay") || k.endsWith("_lang")) && JSON.stringify(before[k]) !== JSON.stringify(cfg[k]))) refreshPreview();
      if (S.view === "settings" && !S.ui.live) renderSettingsPage();
      if (S.view === "wizard" && (S.ui.wz !== 3)) renderWizard();
    },
    glossary: (items) => { S.glossary = items; if (S.view === "settings" && S.ui.tab === "quality") renderSettingsPage(); },
    lessons: (items) => { S.lessons = items; if (S.view === "settings" && S.ui.tab === "quality") renderSettingsPage(); },
    levels: (levels) => { S.ui.levels = levels; paintLevels(); },
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
      S.overlay = d;
      renderOverlayState();
      if (S.view === "settings" && S.ui.tab === "overlay") renderSettingsPage();
    },
    saved: (d) => flashSaved(d.what),
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
    S.fallback = st.fallback; S.sources = st.sources; S.messages = st.messages; S.glossary = st.glossary; S.lessons = st.lessons || [];
    S.version = st.version; S.canWindow = st.can_window; S.overlay = st.overlay || S.overlay; S.record = st.record || S.record;
    const view = S.cfg.setup_done ? (st.view === "wizard" ? "main" : st.view) : "wizard";
    app.dataset.view = S.view = view;
    renderAll();
    const splash = $("splash");
    if (splash) { splash.classList.add("gone"); setTimeout(() => splash.remove(), 400); }
    if (view !== st.view) post("/api/view", { view });
    if (view === "settings") { renderSettings(); if (S.ui.tab === "audio") openDevices(); }
    return true;
  }

  load().then(connect);
})();
