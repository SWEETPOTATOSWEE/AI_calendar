const apiBase = window.__API_BASE__ || "/api";
  let calendar = null;
  let yearViewYear = null;
  let selectedDateStr = null; // YYYY-MM-DD
  let nlpInputComposing = false;

  let confirmState = { mode: null, addItems: [], deleteGroups: [] };
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const APP_CONTEXT = window.__APP_CONTEXT__ || {};
  const APP_MODE = APP_CONTEXT.mode || "local";
  const IS_GOOGLE_MODE = APP_MODE === "google";
  const IS_ADMIN = !!APP_CONTEXT.admin;
  const REASONING_EFFORT_KEY = "calendar_reasoning_effort";
  const ALLOWED_REASONING_EFFORTS = ["low","medium","high"];
  const DEFAULT_REASONING_EFFORT = "low";
  let reasoningEffortValue = DEFAULT_REASONING_EFFORT;
  const undoStack = [];
  const MAX_IMAGE_ATTACHMENTS = 5;
  const MAX_IMAGE_DIMENSION = 1600;
  const MAX_IMAGE_BYTES = 2.5 * 1024 * 1024;
  const nlpImageAttachments = [];
  const nlpConversation = [];
  let imageAttachmentSeq = 1;
  let imageEditorCanvas = null;
  let imageEditorCtx = null;
  let imageEditorOverlay = null;
  let imageEditorSelection = null;
  let imageEditorSelectionCtx = null;
  let imageEditorUndoStack = [];
  const imageEditorState = {
    attachmentId: null,
    drawing: false,
    startX: 0,
    startY: 0,
    pointerId: null
  };
  let currentEventModalContext = null;
  const googleEventCache = {};
  const googleEventFetches = {};
  let googleCacheDirty = false;
  let googleCacheGeneration = 0;
  let googleGlobalLoaderDepth = 0;
  const recurrenceEndSelections = new Map();
  const localEventsCache = { start: null, end: null, items: [] };
  let localCacheDirty = true;
  let localCachePromise = null;
  let initialListLoaded = false;
  let toastSeq = 0;
  const eventsDockQuery = window.matchMedia("(min-width: 981px)");

  function syncEventsPanelDock(){
    const panel = document.getElementById("events-list");
    const mainSlot = document.getElementById("events-main-slot");
    const sideSlot = document.getElementById("events-side-slot");
    if(!panel || !mainSlot || !sideSlot) return;
    const isDetached = eventsDockQuery.matches;
    const target = isDetached ? sideSlot : mainSlot;
    if(panel.parentElement !== target){
      target.appendChild(panel);
    }
    panel.classList.toggle("topbar-block", isDetached);
    panel.classList.toggle("is-detached", isDetached);
    sideSlot.classList.toggle("has-events", isDetached);
  }

  function initEventsPanelDock(){
    syncEventsPanelDock();
    if(typeof eventsDockQuery.addEventListener === "function"){
      eventsDockQuery.addEventListener("change", syncEventsPanelDock);
    }else if(typeof eventsDockQuery.addListener === "function"){
      eventsDockQuery.addListener(syncEventsPanelDock);
    }
  }

  function showToast(message, kind = "warn", durationMs = 3600){
    const text = (message || "").toString().trim();
    if(!text) return;
    const host = document.getElementById("toast-stack");
    if(!host) return;

    toastSeq += 1;
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.setAttribute("role", "status");
    toast.dataset.toastId = String(toastSeq);

    const icon = document.createElement("div");
    icon.className = "toast-icon";
    icon.textContent = "!";

    const msg = document.createElement("div");
    msg.className = "toast-message";
    msg.textContent = text;

    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "닫기");
    close.textContent = "닫기";

    toast.appendChild(icon);
    toast.appendChild(msg);
    toast.appendChild(close);
    host.appendChild(toast);

    const dismiss = () => {
      toast.classList.remove("show");
      const remove = () => toast.remove();
      toast.addEventListener("transitionend", remove, { once: true });
      setTimeout(remove, 350);
    };

    const timer = setTimeout(dismiss, durationMs);
    close.addEventListener("click", () => {
      clearTimeout(timer);
      dismiss();
    });

    requestAnimationFrame(() => toast.classList.add("show"));
  }

  function showWarning(message){
    showToast(message, "warn");
  }

  function buildEventsFetchUrl(startDate, endDate){
    const params = new URLSearchParams();
    if(startDate){
      params.append("start_date", startDate);
    }
    if(endDate){
      params.append("end_date", endDate);
    }
    const query = params.toString();
    return query ? `${apiBase}/events?${query}` : `${apiBase}/events`;
  }

  async function fetchLocalEventsBetween(startDate, endDate){
    const url = buildEventsFetchUrl(startDate, endDate);
    const res = await fetch(url);
    if(!res.ok){
      throw new Error("local events failed");
    }
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  }

  function markLocalCacheDirty(){
    localCacheDirty = true;
  }

  function cacheCoversRange(cache, startDate, endDate){
    return !!cache.start && !!cache.end && startDate >= cache.start && endDate <= cache.end;
  }

  function filterEventsInRange(items, startDate, endDate){
    return (items || []).filter(ev => eventIntersectsRange(ev, startDate, endDate));
  }

  async function getLocalEventsForRange(startDate, endDate){
    if(!startDate || !endDate) return [];
    if(!localCacheDirty && cacheCoversRange(localEventsCache, startDate, endDate)){
      return filterEventsInRange(localEventsCache.items, startDate, endDate);
    }
    if(localCachePromise){
      try{
        await localCachePromise;
      }catch(err){
        // ignore, fallback to fetching below
      }
      if(!localCacheDirty && cacheCoversRange(localEventsCache, startDate, endDate)){
        return filterEventsInRange(localEventsCache.items, startDate, endDate);
      }
    }
    localCachePromise = (async () => {
      const data = await fetchLocalEventsBetween(startDate, endDate);
      localEventsCache.start = startDate;
      localEventsCache.end = endDate;
      localEventsCache.items = Array.isArray(data) ? data : [];
      localCacheDirty = false;
      return localEventsCache.items;
    })().catch((err) => {
      localCacheDirty = true;
      throw err;
    }).finally(() => {
      localCachePromise = null;
    });
    const data = await localCachePromise;
    return filterEventsInRange(data, startDate, endDate);
  }

  function validateDateRange(start, end){
    if(!start || !end){
      return { ok:false, message:"시작·종료 날짜를 모두 선택해주세요." };
    }
    const startMs = Date.parse(start);
    const endMs = Date.parse(end);
    if(Number.isNaN(startMs) || Number.isNaN(endMs)){
      return { ok:false, message:"날짜 형식이 잘못되었습니다." };
    }
    if(endMs < startMs){
      return { ok:false, message:"종료 날짜가 시작 날짜보다 빠릅니다." };
    }
    const diffDays = Math.floor((endMs - startMs) / MS_PER_DAY);
    if(diffDays > 365){
      return { ok:false, message:"범위는 최대 1년까지만 설정할 수 있습니다." };
    }
    return { ok:true };
  }

  function getDeleteScopeOrAlert(){
    const startInput = document.getElementById("delete-scope-start");
    const endInput = document.getElementById("delete-scope-end");
    const start = startInput?.value || "";
    const end = endInput?.value || "";
    const validation = validateDateRange(start, end);
    if(!validation.ok){
      showWarning(validation.message);
      return null;
    }
    return { start, end };
  }

  function setDefaultDateRange(startId, endId, spanDays){
    const startInput = document.getElementById(startId);
    const endInput = document.getElementById(endId);
    if(!startInput || !endInput) return;
    if(startInput.value && endInput.value) return;
    const startDate = new Date();
    const endDate = new Date(startDate.getTime() + spanDays * MS_PER_DAY);
    const fmt = (dt) => dt.toISOString().slice(0,10);
    if(!startInput.value){
      startInput.value = fmt(startDate);
    }
    if(!endInput.value){
      endInput.value = fmt(endDate);
    }
  }

  function toDateStrLocal(d){
    const y = d.getFullYear();
    const m = String(d.getMonth()+1).padStart(2,"0");
    const day = String(d.getDate()).padStart(2,"0");
    return `${y}-${m}-${day}`;
  }

  function normalizeSearchQuery(raw){
    return (raw || "").trim().toLowerCase();
  }

  function getEventDateStr(ev){
    return toDateOnly(ev?.start) || toDateOnly(ev?.end) || "";
  }

  function eventMatchesQuery(ev, query){
    if(!ev || !query) return false;
    const title = (ev.title || "").toString().toLowerCase();
    const location = (ev.location || "").toString().toLowerCase();
    return title.includes(query) || location.includes(query);
  }

  function extractSearchYears(query, baseYear){
    const years = new Set();
    if(Number.isFinite(baseYear)){
      for(let y = baseYear - 1; y <= baseYear + 1; y += 1){
        if(y >= 1970 && y <= 2100){
          years.add(y);
        }
      }
    }
    const matches = (query || "").match(/\b(19|20)\d{2}\b/g) || [];
    matches.forEach((val) => {
      const y = parseInt(val, 10);
      if(Number.isFinite(y) && y >= 1970 && y <= 2100){
        years.add(y);
      }
    });
    return Array.from(years);
  }

  function getYearsFromRange(startDateStr, endDateStr){
    const startYear = getYearFromDateStr(startDateStr);
    const endYear = getYearFromDateStr(endDateStr);
    if(!Number.isFinite(startYear) || !Number.isFinite(endYear)){
      return [];
    }
    const years = [];
    for(let y = startYear; y <= endYear; y += 1){
      years.push(y);
    }
    return years;
  }

  function getCustomSearchRange(backYears, forwardYears){
    const back = Number.isFinite(backYears) ? backYears : 0;
    const forward = Number.isFinite(forwardYears) ? forwardYears : 0;
    if(back <= 0 && forward <= 0){
      return null;
    }
    const today = new Date();
    const start = new Date(today);
    const end = new Date(today);
    start.setFullYear(today.getFullYear() - Math.max(0, back));
    end.setFullYear(today.getFullYear() + Math.max(0, forward));
    return {
      start: toDateStrLocal(start),
      end: toDateStrLocal(end)
    };
  }

  function pickBestMatch(matches){
    if(!Array.isArray(matches) || matches.length === 0){
      return null;
    }
    const today = toDateStrLocal(new Date());
    const dated = matches
      .map((ev) => ({ ev, date: getEventDateStr(ev) }))
      .filter((item) => item.date);
    if(dated.length === 0){
      return matches[0];
    }
    const upcoming = dated
      .filter((item) => item.date >= today)
      .sort((a, b) => a.date.localeCompare(b.date));
    if(upcoming.length > 0){
      return upcoming[0].ev;
    }
    dated.sort((a, b) => b.date.localeCompare(a.date));
    return dated[0].ev;
  }

  function getCalendarViewRange(info){
    const startSource = (info && info.start) ? info.start : new Date();
    const startDate = new Date(startSource);
    const endSource = (info && info.end) ? info.end : startDate;
    let endDate = new Date(endSource);
    endDate = new Date(endDate.getTime() - MS_PER_DAY);
    if(endDate < startDate){
      endDate = new Date(startDate);
    }
    const diffDays = Math.floor((endDate.getTime() - startDate.getTime()) / MS_PER_DAY);
    if(diffDays > 365){
      endDate = new Date(startDate.getTime() + 365 * MS_PER_DAY);
    }
    return {
      startDate,
      endDate,
      startStr: toDateStrLocal(startDate),
      endStr: toDateStrLocal(endDate)
    };
  }

  function updateYearMonthLabel(date){
    const ymLabel = document.getElementById("ym-label");
    const monthLabel = document.getElementById("month-label");
    const y = date.getFullYear();
    const m = date.getMonth() + 1;
    ymLabel.textContent = `${y}`;
    if(monthLabel){
      monthLabel.textContent = `${m}`;
    }
  }

  function setYearViewVisible(isVisible, targetView = "dayGridMonth"){
    const container = document.getElementById("calendar-container");
    const yearView = document.getElementById("year-view");
    if(!container || !yearView) return;

    container.classList.toggle("is-year-view", !!isVisible);
    yearView.classList.toggle("active", !!isVisible);
    yearView.classList.toggle("is-visible", !!isVisible);

    if(!isVisible && calendar){
      requestAnimationFrame(() => calendar.updateSize());
    }
  }

  function refreshYearView(year){
    if(!Number.isFinite(year)) return;
    yearViewYear = year;
    const ymLabel = document.getElementById("ym-label");
    if(ymLabel) ymLabel.textContent = `${yearViewYear}`;
    renderYearView(yearViewYear);
  }

  function renderYearView(year){
    const grid = document.getElementById("year-grid");
    if(!grid || !Number.isFinite(year)) return;
    grid.innerHTML = "";
    const today = new Date();
    const todayKey = toDateStrLocal(today);
    const weekdays = ["일", "월", "화", "수", "목", "금", "토"];

    for(let month = 0; month < 12; month += 1){
      const card = document.createElement("button");
      card.type = "button";
      card.className = "year-card";
      card.setAttribute("aria-label", `${month + 1}월`);
      card.dataset.month = String(month + 1);
      card.dataset.year = String(year);

      const title = document.createElement("div");
      title.className = "year-card-title";
      title.textContent = `${month + 1}`;
      card.appendChild(title);

      const head = document.createElement("div");
      head.className = "mini-weekdays";
      weekdays.forEach((label) => {
        const span = document.createElement("span");
        span.textContent = label;
        head.appendChild(span);
      });
      card.appendChild(head);

      const daysWrap = document.createElement("div");
      daysWrap.className = "mini-days";
      const first = new Date(year, month, 1);
      const startDay = first.getDay();
      const daysInMonth = new Date(year, month + 1, 0).getDate();
      for(let i = 0; i < startDay; i += 1){
        const empty = document.createElement("span");
        empty.className = "mini-day is-empty";
        empty.textContent = ".";
        daysWrap.appendChild(empty);
      }
      for(let d = 1; d <= daysInMonth; d += 1){
        const mm = String(month + 1).padStart(2, "0");
        const dd = String(d).padStart(2, "0");
        const dateStr = `${year}-${mm}-${dd}`;
        const dayEl = document.createElement("span");
        dayEl.className = "mini-day";
        dayEl.textContent = String(d);
        if(dateStr === todayKey){
          dayEl.classList.add("is-today");
        }
        daysWrap.appendChild(dayEl);
      }
      card.appendChild(daysWrap);

      card.addEventListener("click", () => {
        const mm = String(month + 1).padStart(2, "0");
        const targetDate = `${year}-${mm}-01`;
        calendar.changeView("dayGridMonth");
        calendar.gotoDate(targetDate);
        setActiveView("dayGridMonth");
        setSelectedDate(targetDate);
        loadEventListForDate(targetDate);
        setYearViewVisible(false, "dayGridMonth");
      });

      grid.appendChild(card);
    }
  }

  function setActiveView(viewType){
    document.querySelectorAll("[data-cal-view]").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.calView === viewType);
    });
    requestAnimationFrame(updateViewSwitchIndicators);
  }

  function updateViewSwitchIndicators(){
    document.querySelectorAll(".view-switch").forEach((wrap) => {
      const indicator = wrap.querySelector(".view-switch-indicator");
      const activeBtn = wrap.querySelector(".view-btn.active") || wrap.querySelector(".view-btn");
      if(!indicator || !activeBtn) return;
      if(wrap.offsetWidth === 0 || wrap.offsetHeight === 0) return;
      const x = activeBtn.offsetLeft;
      const y = activeBtn.offsetTop;
      indicator.style.width = `${activeBtn.offsetWidth}px`;
      indicator.style.height = `${activeBtn.offsetHeight}px`;
      indicator.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    });
  }

  function setSelectedDate(dateStr){
    selectedDateStr = dateStr;
    if(calendar){
      syncSelectedDayHighlight();
    }
  }

  function syncSelectedDayHighlight(){
    const target = selectedDateStr;
    if(!target){
      return;
    }
    requestAnimationFrame(() => {
      document.querySelectorAll("#calendar .fc-daygrid-day[data-date]").forEach(cell => {
        cell.classList.toggle("selected-day", cell.getAttribute("data-date") === target);
      });
      document.querySelectorAll("#calendar .fc-col-header-cell[data-date]").forEach(cell => {
        cell.classList.toggle("selected-day-header", cell.getAttribute("data-date") === target);
      });
      document.querySelectorAll("#calendar .fc-timegrid-col[data-date]").forEach(col => {
        col.classList.toggle("selected-day", col.getAttribute("data-date") === target);
      });
    });
  }

  function formatCreatedAt(ts){
    if(!ts) return "";
    try{
      const [d,t] = ts.split("T");
      const [y,m,da] = d.split("-").map(x => parseInt(x,10));
      const [hh,mm] = (t || "00:00").split(":").map(x => parseInt(x,10));
      const dt = new Date(Date.UTC(y, (m||1)-1, da||1, hh||0, mm||0));
      return new Intl.DateTimeFormat("ko-KR", {
        month:"2-digit", day:"2-digit",
        hour:"2-digit", minute:"2-digit",
        hour12:false, timeZone:"Asia/Seoul"
      }).format(dt);
    }catch{
      return ts;
    }
  }

  function setupShadowAutoGrow(textareaId){
    const ta = document.getElementById(textareaId);
    if(!ta) return;

    const wrap = ta.closest(".composer-input-wrap");
    const shadow = document.createElement("div");
    shadow.setAttribute("aria-hidden", "true");
    shadow.style.position = "absolute";
    shadow.style.top = "0";
    shadow.style.left = "-9999px";
    shadow.style.visibility = "hidden";
    shadow.style.pointerEvents = "none";
    shadow.style.whiteSpace = "pre-wrap";
    shadow.style.wordBreak = "break-word";
    shadow.style.overflowWrap = "break-word";
    shadow.style.padding = "0";
    shadow.style.margin = "0";
    shadow.style.border = "0";
    shadow.style.boxSizing = "border-box";
    document.body.appendChild(shadow);
    let base = 28;
    let rafId = null;
    let shadowPadY = 0;
    let shadowLineHeight = 0;

    const syncShadowStyle = () => {
      const cs = getComputedStyle(ta);
      shadow.style.fontFamily = cs.fontFamily;
      shadow.style.fontSize = cs.fontSize;
      shadow.style.fontWeight = cs.fontWeight;
      shadow.style.letterSpacing = cs.letterSpacing;
      const fs = parseFloat(cs.fontSize) || 14;
      const lh = parseFloat(cs.lineHeight);
      shadowLineHeight = Number.isFinite(lh) ? lh : fs * 1.45;
      shadow.style.lineHeight = `${shadowLineHeight}px`;
      const padTop = parseFloat(cs.paddingTop) || 0;
      const padRight = parseFloat(cs.paddingRight) || 0;
      const padBottom = parseFloat(cs.paddingBottom) || 0;
      const padLeft = parseFloat(cs.paddingLeft) || 0;
      shadowPadY = padTop + padBottom;
      shadow.style.paddingTop = `${padTop}px`;
      shadow.style.paddingRight = `${padRight}px`;
      shadow.style.paddingBottom = `${padBottom}px`;
      shadow.style.paddingLeft = `${padLeft}px`;
    };

    const getLineHeight = () => {
      if(shadowLineHeight) return shadowLineHeight;
      const cs = getComputedStyle(ta);
      const lh = parseFloat(cs.lineHeight);
      if(Number.isFinite(lh)) return lh;
      const fs = parseFloat(cs.fontSize) || 14;
      return fs * 1.45;
    };

    const getInlineWidth = () => {
      if(!wrap) return ta.clientWidth;
      const style = getComputedStyle(wrap);
      const padLeft = parseFloat(style.paddingLeft) || 0;
      const padRight = parseFloat(style.paddingRight) || 0;
      const gap = parseFloat(style.getPropertyValue("--composer-gap")) || 8;
      const left = wrap.querySelector(".composer-left");
      const action = wrap.querySelector(".inline-action");
      const leftW = left ? left.offsetWidth : 0;
      const actionW = action ? action.offsetWidth : 0;
      let gaps = 0;
      if(leftW && actionW) gaps = gap * 2;
      else if(leftW || actionW) gaps = gap;
      const width = wrap.clientWidth - padLeft - padRight - leftW - actionW - gaps;
      return Math.max(Math.floor(width), 0);
    };

    const measureInline = (value) => {
      syncShadowStyle();
      const lineHeight = getLineHeight();
      if(!wrap){
        return { inlineHeight: base, lineHeight };
      }
      const width = getInlineWidth();
      if(width <= 0){
        return { inlineHeight: base, lineHeight };
      }
      shadow.style.width = `${width}px`;
      shadow.textContent = value || "";
      const totalHeight = Math.ceil(shadow.scrollHeight || 0);
      const inlineHeight = Math.max(0, totalHeight - shadowPadY);
      return { inlineHeight, lineHeight };
    };

    const shouldUseMultiline = (value, metrics) => {
      const text = value || "";
      if(text.includes("\n")) return true;
      if(text.trim() === "") return false;
      if(!wrap) return false;
      const lineHeight = metrics?.lineHeight || getLineHeight();
      const measuredHeight = metrics?.inlineHeight || 0;
      const lineCount = Math.max(1, Math.ceil(measuredHeight / lineHeight));
      return lineCount >= 2;
    };

    const updateWrap = (value, metrics) => {
      if(!wrap) return;
      const nextIsMultiline = shouldUseMultiline(value, metrics);
      const currentIsMultiline = wrap.classList.contains("is-multiline");
      if(currentIsMultiline === nextIsMultiline) return;

      const targets = [
        wrap.querySelector(".composer-left"),
        ta,
        wrap.querySelector(".inline-action")
      ].filter(Boolean);

      const firstRects = targets.map(el => el.getBoundingClientRect());
      wrap.classList.toggle("is-multiline", nextIsMultiline);
      const lastRects = targets.map(el => el.getBoundingClientRect());

      targets.forEach((el, idx) => {
        const first = firstRects[idx];
        const last = lastRects[idx];
        const dx = first.left - last.left;
        const dy = first.top - last.top;
        if(!dx && !dy) return;

        if(el.getAnimations){
          el.getAnimations().forEach(anim => anim.cancel());
        }
        const keyframes = [
          { transform: `translate(${dx}px, ${dy}px)` },
          { transform: "translate(0, 0)" }
        ];
        if(el !== ta){
          keyframes[0].opacity = 0.35;
          keyframes[1].opacity = 1;
        }
        el.animate(keyframes, {
          duration:240,
          easing:"cubic-bezier(.22,.61,.36,1)",
          fill:"both"
        });
      });
    };

    const computeBase = () => {
      const prev = ta.value;
      ta.value = "";
      ta.style.height = "auto";
      const measured = ta.scrollHeight;
      base = Math.max(measured || 0, 28);
      ta.value = prev;
    };

    const resize = () => {
      const value = ta.value ?? "";
      if(value.trim() === ""){
        if(rafId) cancelAnimationFrame(rafId);
        updateWrap("", { inlineHeight: base, lineHeight: getLineHeight() });
        ta.style.height = base + "px";
        return;
      }

      const metrics = measureInline(value);
      updateWrap(value, metrics);
      const startH = ta.getBoundingClientRect().height;
      ta.style.height = "auto";
      const actualHeight = ta.scrollHeight;
      const inlineHeight = Math.max(metrics.inlineHeight || 0, base);
      let target = Math.max(actualHeight, inlineHeight, base);

      if(target <= base + 1){ target = base; }
      ta.style.height = startH + "px";

      if(rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        ta.style.height = target + "px";
      });
    };

    requestAnimationFrame(() => {
      computeBase();
      ta.style.height = base + "px";
      resize();
    });

    ["input","focus"].forEach(evt => ta.addEventListener(evt, resize));
    const onResize = () => {
      if(rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        syncShadowStyle();
        computeBase();
        const metrics = measureInline(ta.value || "");
        updateWrap(ta.value || "", metrics);
        resize();
      });
    };
    window.addEventListener("resize", onResize);
  }

  function toDateOnly(value){
    if(!value || value.length < 10) return null;
    return value.slice(0,10);
  }

  function addDaysToDateStr(dateStr, days){
    if(!dateStr) return null;
    const parts = dateStr.split("-").map(part => parseInt(part, 10));
    if(parts.length !== 3 || parts.some(n => Number.isNaN(n))) return null;
    const [y, m, d] = parts;
    const dt = new Date(Date.UTC(y, m - 1, d));
    dt.setUTCDate(dt.getUTCDate() + days);
    const yy = dt.getUTCFullYear();
    const mm = String(dt.getUTCMonth() + 1).padStart(2,"0");
    const dd = String(dt.getUTCDate()).padStart(2,"0");
    return `${yy}-${mm}-${dd}`;
  }

  function getEventDateSpan(ev){
    if(!ev) return null;
    const startDate = toDateOnly(ev.start) || toDateOnly(ev.end);
    if(!startDate) return null;
    let endDate = toDateOnly(ev.end) || startDate;
    if((ev.allDay === true || ev.all_day === true) && ev.end && typeof ev.end === "string" && ev.end.endsWith("T00:00")){
      const adjusted = addDaysToDateStr(endDate, -1);
      if(adjusted) endDate = adjusted;
    }
    if(endDate < startDate){
      endDate = startDate;
    }
    return { start: startDate, end: endDate };
  }

  function eventCoversDate(ev, dateStr){
    if(!ev || !dateStr) return false;
    const span = getEventDateSpan(ev);
    if(!span) return false;
    return dateStr >= span.start && dateStr <= span.end;
  }

  function eventIntersectsRange(ev, startDateStr, endDateStr){
    const span = getEventDateSpan(ev);
    if(!span || !startDateStr || !endDateStr) return false;
    if(span.end < startDateStr) return false;
    if(span.start > endDateStr) return false;
    return true;
  }

  function isAllDayRange(start, end){
    if(!start) return false;
    const startDate = toDateOnly(start);
    if(!startDate) return false;
    const startTime = start.length >= 16 ? start.slice(11,16) : "00:00";
    if(startTime !== "00:00") return false;
    if(!end){
      return true;
    }
    const endDate = toDateOnly(end);
    if(!endDate) return true;
    const endTime = end.length >= 16 ? end.slice(11,16) : "23:59";
    if(endDate < startDate) return false;
    if(endTime === "00:00"){
      return endDate > startDate;
    }
    return endTime === "23:59" || endTime === "00:00";
  }

  function fmtRange(start, end, allDayOverride){
    if(!start) return "";
    const startDate = toDateOnly(start);
    const isAllDay = (typeof allDayOverride === "boolean")
      ? allDayOverride
      : isAllDayRange(start, end);
    if(isAllDay){
      const endDate = toDateOnly(end);
      if(endDate && startDate && endDate !== startDate){
        return `${startDate}~${endDate} 하루종일`;
      }
      return `${startDate || ""} 하루종일`;
    }
    const st = start.slice(11,16);
    if(end) return `${startDate || ""} ${st}–${end.slice(11,16)}`;
    return `${startDate || ""} ${st}`;
  }

  function formatEventMeta(ev){
    if(!ev) return "";
    const startStr = ev.start || "";
    const endStr = ev.end || null;
    const isAllDay = (ev.all_day === true) || isAllDayRange(startStr, endStr);
    if(isAllDay){
      const startDate = toDateOnly(startStr);
      const endDate = toDateOnly(endStr);
      let label = "하루종일";
      if(startDate && endDate && startDate !== endDate){
        label = `하루종일 · ${startDate}~${endDate}`;
      }
      return ev.location ? `${label} · ${ev.location}` : label;
    }
    const timePart = startStr.length >= 16 ? startStr.slice(11,16) : "";
    const label = timePart ? `시작 ${timePart}` : "시간 없음";
    return ev.location ? `${label} · ${ev.location}` : label;
  }

  function closeEventModal(){
    const overlay = document.getElementById("event-modal-overlay");
    if(!overlay) return;
    overlay.classList.remove("active");
    overlay.style.display = "none";
    currentEventModalContext = null;
  }

  function setEventModalDeleteVisible(isVisible){
    const deleteBtn = document.getElementById("event-modal-delete");
    if(!deleteBtn) return;
    deleteBtn.style.display = isVisible ? "inline-flex" : "none";
  }

  function openEventModal(eventInfo){
    const overlay = document.getElementById("event-modal-overlay");
    if(!overlay) return;
    const titleEl = document.getElementById("event-modal-title");
    const subEl = document.getElementById("event-modal-sub");
    const titleInput = document.getElementById("event-modal-input-title");
    const startDateInput = document.getElementById("event-modal-input-start-date");
    const startTimeInput = document.getElementById("event-modal-input-start-time");
    const endDateInput = document.getElementById("event-modal-input-end-date");
    const endTimeInput = document.getElementById("event-modal-input-end-time");
    const locationInput = document.getElementById("event-modal-input-location");
    const notesInput = document.getElementById("event-modal-input-notes");
    if(!titleInput || !startDateInput || !startTimeInput || !endDateInput || !endTimeInput || !locationInput || !notesInput){
      return;
    }

    const toLocalDate = (value) => {
      if(!value) return "";
      const dt = new Date(value);
      if(Number.isNaN(dt.getTime())){
        return (value || "").slice(0, 10);
      }
      const offset = dt.getTimezoneOffset();
      const adjusted = new Date(dt.getTime() - offset * 60 * 1000);
      return adjusted.toISOString().slice(0, 10);
    };

    const toLocalTime = (value) => {
      if(!value) return "";
      const dt = new Date(value);
      if(Number.isNaN(dt.getTime())) return "";
      return dt.toTimeString().slice(0, 5);
    };

    titleEl.textContent = eventInfo.title || "(제목 없음)";
    subEl.textContent = (eventInfo.source === "google") ? "Google 일정" : "내 일정";
    titleInput.value = eventInfo.title || "";
    startDateInput.value = toLocalDate(eventInfo.start);
    startTimeInput.value = eventInfo.allDay ? "" : toLocalTime(eventInfo.start);
    endDateInput.value = toLocalDate(eventInfo.end);
    endTimeInput.value = eventInfo.allDay ? "" : toLocalTime(eventInfo.end);
    locationInput.value = eventInfo.location || "";
    notesInput.value = eventInfo.notes || "";

    const numericId = parseInt(eventInfo.id, 10);
    currentEventModalContext = {
      source: eventInfo.source || "local",
      localId: (eventInfo.source === "google") ? null : (Number.isFinite(numericId) ? numericId : null),
      googleId: eventInfo.googleId || null,
      isNew: false
    };

    setEventModalDeleteVisible(true);
    overlay.style.display = "flex";
    requestAnimationFrame(() => overlay.classList.add("active"));
  }

  function openCreateEventModal(dateStr){
    const overlay = document.getElementById("event-modal-overlay");
    if(!overlay) return;
    const titleEl = document.getElementById("event-modal-title");
    const subEl = document.getElementById("event-modal-sub");
    const titleInput = document.getElementById("event-modal-input-title");
    const startDateInput = document.getElementById("event-modal-input-start-date");
    const startTimeInput = document.getElementById("event-modal-input-start-time");
    const endDateInput = document.getElementById("event-modal-input-end-date");
    const endTimeInput = document.getElementById("event-modal-input-end-time");
    const locationInput = document.getElementById("event-modal-input-location");
    const notesInput = document.getElementById("event-modal-input-notes");
    if(!titleInput || !startDateInput || !startTimeInput || !endDateInput || !endTimeInput || !locationInput || !notesInput){
      return;
    }

    titleEl.textContent = "새 일정";
    subEl.textContent = IS_GOOGLE_MODE ? "Google 일정" : "내 일정";
    titleInput.value = "";
    startDateInput.value = dateStr || "";
    startTimeInput.value = "";
    endDateInput.value = dateStr || "";
    endTimeInput.value = "";
    locationInput.value = "";
    notesInput.value = "";

    currentEventModalContext = {
      source: IS_GOOGLE_MODE ? "google" : "local",
      localId: null,
      googleId: null,
      isNew: true
    };

    setEventModalDeleteVisible(false);
    overlay.style.display = "flex";
    requestAnimationFrame(() => overlay.classList.add("active"));
    titleInput.focus();
  }

  async function saveEventModal(){
    if(!currentEventModalContext) return;
    const titleInput = document.getElementById("event-modal-input-title");
    const startDateInput = document.getElementById("event-modal-input-start-date");
    const startTimeInput = document.getElementById("event-modal-input-start-time");
    const endDateInput = document.getElementById("event-modal-input-end-date");
    const endTimeInput = document.getElementById("event-modal-input-end-time");
    const locationInput = document.getElementById("event-modal-input-location");
    const notesInput = document.getElementById("event-modal-input-notes");
    if(!titleInput || !startDateInput || !startTimeInput || !endDateInput || !endTimeInput || !locationInput || !notesInput){
      return;
    }

    const title = titleInput.value.trim();
    const startDate = startDateInput.value;
    const startTime = startTimeInput.value;
    const endDate = endDateInput.value;
    const endTime = endTimeInput.value;
    const locationValue = locationInput.value.trim();

    if(!title){
      showWarning("제목을 입력해주세요.");
      return;
    }
    if(!startDate){
      showWarning("시작 날짜를 입력해주세요.");
      return;
    }

    const combineDateTime = (dateVal, timeVal) => {
      if(!dateVal) return "";
      if(!timeVal) return `${dateVal}T00:00`;
      return `${dateVal}T${timeVal}`;
    };

    const payload = {
      title,
      start: combineDateTime(startDate, startTime),
      end: endDate ? combineDateTime(endDate, endTime || "00:00") : null,
      location: locationValue || null,
      all_day: !startTime && !endTime
    };

    if(payload.end){
      const startMs = Date.parse(payload.start);
      const endMs = Date.parse(payload.end);
      if(!Number.isNaN(startMs) && !Number.isNaN(endMs) && endMs < startMs){
        showWarning("종료 시각이 시작 시각보다 빠릅니다.");
        return;
      }
    }

    const headers = { "Content-Type": "application/json" };
    let touchedGoogleEvent = false;
    try{
      const isNew = !!currentEventModalContext.isNew
        || (!currentEventModalContext.localId && !currentEventModalContext.googleId);
      if(isNew){
        const res = await fetch("/api/events", {
          method:"POST",
          headers,
          body: JSON.stringify(payload)
        });
        if(!res.ok){
          showWarning("일정 추가에 실패했습니다.");
          return;
        }
        const created = await res.json();
        recordUndoBatch([created]);
        markLocalCacheDirty();
        if(IS_GOOGLE_MODE){
          markGoogleCacheDirty();
        }
        closeEventModal();
        await refreshAll();
        return;
      }
      if(currentEventModalContext.source === "google"){
        const googleId = currentEventModalContext.googleId || "";
        if(googleId){
          await fetch(`/api/google/events/${encodeURIComponent(googleId)}`, {
            method:"PATCH",
            headers,
            body: JSON.stringify(payload)
          });
          touchedGoogleEvent = true;
        }
      }else{
        const localId = currentEventModalContext.localId;
        if(localId){
          await fetch(`/api/events/${localId}`, {
            method:"PATCH",
            headers,
            body: JSON.stringify(payload)
          });
          markLocalCacheDirty();
        }
      }
      if(touchedGoogleEvent){
        markGoogleCacheDirty();
      }
      closeEventModal();
      await refreshAll();
    }catch(err){
      console.error(err);
      showWarning("일정 수정에 실패했습니다.");
    }
  }

  async function deleteEventModal(){
    if(!currentEventModalContext) return;
    try{
      if(currentEventModalContext.source === "google"){
        const googleId = currentEventModalContext.googleId || "";
        if(!googleId){
          showWarning("삭제할 일정이 없습니다.");
          return;
        }
        await deleteGoogleEventById(googleId);
      }else{
        const localId = currentEventModalContext.localId;
        if(!localId){
          showWarning("삭제할 일정이 없습니다.");
          return;
        }
        await fetch(`/api/events/${localId}`, { method:"DELETE" });
        markLocalCacheDirty();
      }
      closeEventModal();
      await refreshAll();
    }catch(err){
      console.error(err);
      showWarning("일정 삭제에 실패했습니다.");
    }
  }

  async function deleteGoogleEventById(eventId){
    if(!eventId) return;
    const res = await fetch(apiBase + "/google/events/" + encodeURIComponent(eventId), {
      method:"DELETE"
    });
    if(res.ok){
      markGoogleCacheDirty();
    }
  }

  function updateUndoButton(){
    const has = undoStack.length > 0;
    const count = undoStack.length;
    const label = has ? `되돌리기 (${count})` : "되돌리기";

    const mainBtn = document.getElementById("undo-last-btn");
    if(mainBtn){
      mainBtn.disabled = !has;
      mainBtn.setAttribute("aria-label", label);
      mainBtn.setAttribute("title", label);
      const badge = document.getElementById("undo-count-badge");
      if(badge){
        badge.textContent = has ? String(count) : "";
        badge.classList.toggle("is-hidden", !has);
      }
    }

    const drawerBtn = document.getElementById("drawer-undo-btn");
    if(drawerBtn){
      drawerBtn.disabled = !has;
      drawerBtn.textContent = label;
    }
  }

  function recordUndoBatch(events){
    if(!Array.isArray(events) || events.length === 0) return;
    const batch = events
      .map(ev => ({
        localId: (typeof ev.id === "number" || /^\d+$/.test(String(ev.id || ""))) ? parseInt(ev.id, 10) : null,
        googleId: ev.google_event_id || null
      }))
      .filter(item => item.localId || item.googleId);
    if(!batch.length) return;
    undoStack.push(batch);
    updateUndoButton();
  }

  async function undoLastBatch(){
    if(undoStack.length === 0) return;
    const batch = undoStack.pop();
    updateUndoButton();
    const localIds = batch.map(item => item.localId).filter(id => Number.isFinite(id));
    const googleIds = batch.map(item => item.googleId).filter(id => !!id);
    try{
      if(localIds.length){
        await fetch(apiBase + "/delete-by-ids", {
          method:"POST",
          headers:{ "Content-Type":"application/json" },
          body: JSON.stringify({ ids: localIds })
        });
        markLocalCacheDirty();
      }
      for(const gid of googleIds){
        await deleteGoogleEventById(gid);
      }
      await refreshAll();
    }catch(err){
      console.error(err);
      showWarning("되돌리기에 실패했습니다.");
    }
  }

  async function loadEventListForDate(dateStr){
    const targetDate = dateStr || "";
    const ul = document.getElementById("events-ul");
    ul.innerHTML = "";

    if(!targetDate){
      return;
    }

    let dayEvents = [];
    if(!IS_GOOGLE_MODE){
      let events = [];
      try{
        events = await getLocalEventsForRange(targetDate, targetDate);
      }catch(err){
        console.error(err);
        events = [];
      }
      dayEvents = events
        .filter(ev => eventCoversDate(ev, targetDate))
        .map(ev => ({ ...ev, _source: "local" }));
    }

    let googleDayEvents = [];
    if(IS_GOOGLE_MODE){
      try{
        googleDayEvents = collectGoogleEventsForDate(targetDate)
          .map(ev => ({ ...ev, _source: "google" }));
      }catch(err){
        console.error(err);
      }
    }

    const combined = [...dayEvents, ...googleDayEvents];

    combined.sort((a, b) => {
      const aStart = a.start || "";
      const bStart = b.start || "";
      if(aStart === bStart){
        return (a.title || "").localeCompare(b.title || "");
      }
      return aStart.localeCompare(bStart);
    });
    if(combined.length === 0){
      ul.innerHTML = "<li class='events-empty'>일정 없음</li>";
      return;
    }

    for(const ev of combined){
      const li = document.createElement("li");
      li.addEventListener("click", () => {
        openEventModal({
          id: ev.id,
          title: ev.title,
          start: ev.start,
          end: ev.end,
          location: ev.location,
          notes: "",
          source: ev._source || "local",
          googleId: ev.google_event_id || ev.id,
          allDay: ev.all_day
        });
      });

      const dot = document.createElement("div");
      dot.className = "event-dot";

      const info = document.createElement("div");
      info.className = "event-info";

      const title = document.createElement("div");
      title.className = "event-title";
      title.textContent = ev.title || "";

      const meta = document.createElement("div");
      meta.className = "event-meta";
      const locationIcon = document.createElement("span");
      locationIcon.className = "location-icon";
      locationIcon.setAttribute("aria-hidden", "true");
      locationIcon.innerHTML = "<svg viewBox='0 0 24 24' role='img' focusable='false' aria-hidden='true'><path d='M12 2c3.9 0 7 3.1 7 7 0 5.2-6 12.3-6.3 12.6-.4.4-1 .4-1.4 0C11 21.3 5 14.2 5 9c0-3.9 3.1-7 7-7zm0 4.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z'/></svg>";

      if(ev.location){
        const locationText = document.createElement("span");
        locationText.textContent = ev.location;
        meta.appendChild(locationIcon);
        meta.appendChild(locationText);
      }

      info.appendChild(title);
      if(meta.childNodes.length){
        info.appendChild(meta);
      }

      const timeBox = document.createElement("div");
      timeBox.className = "event-time";
      const startStr = ev.start || "";
      const endStr = ev.end || "";
      const isAllDay = (ev.all_day === true) || isAllDayRange(startStr, endStr);

      if(isAllDay){
        const line = document.createElement("div");
        line.className = "time-line";
        line.textContent = "하루종일";
        timeBox.appendChild(line);
      }else{
        const startLine = document.createElement("div");
        startLine.className = "time-line";
        startLine.textContent = startStr.length >= 16 ? startStr.slice(11,16) : "시작 없음";

        const endLine = document.createElement("div");
        endLine.className = "time-line";
        endLine.textContent = endStr.length >= 16 ? endStr.slice(11,16) : "종료 없음";

        timeBox.appendChild(startLine);
        timeBox.appendChild(endLine);
      }

      li.appendChild(dot);
      li.appendChild(info);
      li.appendChild(timeBox);
      ul.appendChild(li);
    }
  }

  // Recent added modal
  function closeRecentModal(){
    document.getElementById("recent-overlay").style.display = "none";
    const list = document.getElementById("recent-list");
    if(list) list.innerHTML = "";
  }

  async function loadRecentList(){
    const list = document.getElementById("recent-list");
    if(!list) return;
    list.innerHTML = "<div style='padding:10px; color:var(--muted); font-weight:800;'>불러오는 중...</div>";
    try{
      const res = await fetch(apiBase + "/recent-events");
      if(!res.ok){
        list.innerHTML = "<div style='padding:10px; color:var(--muted); font-weight:800;'>불러오기 실패</div>";
        return;
      }
      const data = await res.json();
      renderRecentList(Array.isArray(data) ? data : []);
    }catch(err){
      console.error(err);
      list.innerHTML = "<div style='padding:10px; color:var(--muted); font-weight:800;'>불러오기 실패</div>";
    }
  }

  async function openRecentModal(){
    const overlay = document.getElementById("recent-overlay");
    if(!overlay) return;
    overlay.style.display = "flex";
    await loadRecentList();
  }

  function refreshRecentIfOpen(){
    const overlay = document.getElementById("recent-overlay");
    if(overlay && overlay.style.display === "flex"){
      loadRecentList();
    }
  }

  function renderRecentList(items){
    const list = document.getElementById("recent-list");
    if(!list) return;
    if(!items.length){
      list.innerHTML = "<div style='padding:10px; color:var(--muted); font-weight:800;'>최근 14일 내 추가된 일정이 없습니다.</div>";
      return;
    }
    list.innerHTML = "";
    items.forEach((ev) => {
      const source = ev.source || "local";
      const row = document.createElement("div");
      row.className = "recent-item";

      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = (source === "google") ? "Google" : (ev.all_day ? "하루종일" : "일정");

      const main = document.createElement("div");
      main.className = "recent-main";

      const l1 = document.createElement("div");
      l1.className = "recent-line1";
      l1.textContent = ev.title || "(제목 없음)";

      const l2 = document.createElement("div");
      l2.className = "recent-line2";
      l2.textContent = fmtRange(ev.start, ev.end, ev.all_day) + (ev.location ? ` · ${ev.location}` : "");

      const meta = document.createElement("div");
      meta.className = "recent-meta";
      meta.textContent = `추가: ${formatCreatedAt(ev.created_at || ev.created || "")}`;

      main.appendChild(l1);
      main.appendChild(l2);
      main.appendChild(meta);

      const del = document.createElement("button");
      del.className = "recent-delete";
      del.type = "button";
      del.textContent = "삭제";
      del.addEventListener("click", async () => {
        if(!confirm("이 일정을 삭제할까요?")) return;
        try{
          if(source === "google"){
            await deleteGoogleEventById(ev.google_event_id || ev.id);
          }else{
            await fetch(apiBase + "/events/" + ev.id, { method:"DELETE" });
            markLocalCacheDirty();
          }
          await refreshAll();
          await loadRecentList();
        }catch(err){
          console.error(err);
          showWarning("삭제에 실패했습니다.");
        }
      });

      row.appendChild(badge);
      row.appendChild(main);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  async function refreshAll(){
    if(IS_GOOGLE_MODE && googleCacheDirty){
      clearGoogleEventCache();
      googleCacheDirty = false;
    }
    if(calendar) calendar.refetchEvents();
    if(selectedDateStr) await loadEventListForDate(selectedDateStr);
    refreshRecentIfOpen();
  }

  async function createEvent(e){
    e.preventDefault();

    const title = document.getElementById("title").value.trim();
    const start = document.getElementById("start").value;
    const end = document.getElementById("end").value;
    const location = document.getElementById("location").value.trim();

    if(!title || !start){
      showWarning("제목과 시작 시각은 필수입니다.");
      return;
    }

    const payload = { title, start, end: end || null, location: location || null };

    const res = await fetch(apiBase + "/events", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });

    if(res.ok){
      const created = await res.json();
      recordUndoBatch([created]);
      markLocalCacheDirty();
    }else{
      showWarning("일정 추가에 실패했습니다.");
      return;
    }

    document.getElementById("event-form").reset();
    await refreshAll();
  }

  function setUnifiedMode(isDelete){
    const btn = document.getElementById("nlp-action-btn");
    const loaderEl = document.querySelector("#nlp-unified-loader .loader");
    const scopeControls = document.getElementById("delete-scope-controls");
    if(!btn) return;

    btn.classList.toggle("mode-delete", isDelete);
    btn.classList.toggle("mode-add", !isDelete);
    if(isDelete){
      resetNlpConversation();
    }

    if(loaderEl){
      loaderEl.classList.toggle("is-delete", isDelete);
    }
    if(scopeControls){
      scopeControls.style.display = isDelete ? "block" : "none";
    }
  }

  function setUnifiedBusy(isBusy){
    const btn = document.getElementById("nlp-action-btn");
    const loaderWrap = document.getElementById("nlp-unified-loader");
    if(!btn || !loaderWrap) return;

    btn.disabled = !!isBusy;
    btn.classList.toggle("scale-0", !!isBusy);
    btn.classList.toggle("scale-1", !isBusy);
    loaderWrap.classList.toggle("scale-1", !!isBusy);
    loaderWrap.classList.toggle("scale-0", !isBusy);
  }

  function setGlobalLoadingOverlayVisible(visible, message){
    const overlay = document.getElementById("global-loading-overlay");
    if(!overlay) return;
    if(message){
      const textEl = overlay.querySelector(".global-loading-text");
      if(textEl) textEl.textContent = message;
    }
    overlay.classList.toggle("active", !!visible);
    if(document && document.body){
      document.body.classList.toggle("global-loading-active", !!visible);
    }
  }

  function pushGlobalLoading(message){
    googleGlobalLoaderDepth += 1;
    setGlobalLoadingOverlayVisible(true, message || "불러오는 중..");
  }

  function popGlobalLoading(){
    googleGlobalLoaderDepth = Math.max(googleGlobalLoaderDepth - 1, 0);
    if(googleGlobalLoaderDepth === 0){
      setGlobalLoadingOverlayVisible(false);
    }
  }

  function markGoogleCacheDirty(){
    if(!IS_GOOGLE_MODE) return;
    googleCacheDirty = true;
  }

  function clearGoogleEventCache(){
    googleCacheGeneration += 1;
    Object.keys(googleEventCache).forEach((key) => delete googleEventCache[key]);
    Object.keys(googleEventFetches).forEach((key) => delete googleEventFetches[key]);
  }

  function normalizeGoogleEvent(ev){
    if(!ev) return null;
    let start = ev.start || "";
    const end = ev.end || null;
    if(!start && end){
      start = end;
    }
    if(!start) return null;
    const unique = ev.id || start || end || ev.html_link || `${Date.now()}-${Math.random()}`;
    return {
      id:`google:${unique}`,
      google_event_id: ev.id || "",
      title: ev.title || "(제목 없음)",
      start,
      end,
      location: ev.location || "",
      all_day: !!ev.all_day,
      source:"google"
    };
  }

  function getYearFromDateStr(value){
    if(!value || value.length < 4) return null;
    const num = parseInt(value.slice(0,4), 10);
    return Number.isFinite(num) ? num : null;
  }


  async function ensureGoogleEventsForYear(year){
    if(!IS_GOOGLE_MODE || !Number.isFinite(year)) return [];
    if(Array.isArray(googleEventCache[year])) return googleEventCache[year];
    if(googleEventFetches[year]) return googleEventFetches[year];

    const generationAtStart = googleCacheGeneration;
    const message = "불러오는 중..";
    const promise = (async () => {
      pushGlobalLoading(message);
      try{
        const params = new URLSearchParams({
          start_date: `${year}-01-01`,
          end_date: `${year}-12-31`
        });
        const res = await fetch(apiBase + "/google/events?" + params.toString());
        if(!res.ok){
          throw new Error("Google 일정 불러오기 실패");
        }
        const raw = await res.json();
        const normalized = (Array.isArray(raw) ? raw : []).map(normalizeGoogleEvent).filter(Boolean);
        if(generationAtStart === googleCacheGeneration){
          googleEventCache[year] = normalized;
        }
        return normalized;
      }finally{
        popGlobalLoading();
        delete googleEventFetches[year];
      }
    })().catch((err) => {
      console.error(err);
      throw err;
    });

    googleEventFetches[year] = promise;
    return promise;
  }

  async function ensureGoogleEventsForYears(years){
    if(!IS_GOOGLE_MODE) return;
    const uniqueYears = Array.from(new Set((years || []).filter((y) => Number.isFinite(y))));
    if(!uniqueYears.length) return;
    await Promise.all(uniqueYears.map((year) => ensureGoogleEventsForYear(year)));
  }

  async function ensureGoogleEventsForDate(dateStr){
    if(!IS_GOOGLE_MODE || !dateStr) return;
    const year = getYearFromDateStr(dateStr);
    if(Number.isFinite(year)){
      await ensureGoogleEventsForYear(year);
    }
  }

  function collectGoogleEventsForDate(dateStr){
    if(!IS_GOOGLE_MODE || !dateStr) return [];
    const results = [];
    Object.values(googleEventCache).forEach((bucket) => {
      if(!Array.isArray(bucket)) return;
      bucket.forEach((ev) => {
        if(eventCoversDate(ev, dateStr)){
          results.push(ev);
        }
      });
    });
    return results;
  }

  function collectGoogleEventsBetween(startDateStr, endDateStr){
    if(!IS_GOOGLE_MODE || !startDateStr || !endDateStr) return [];
    const results = [];
    Object.values(googleEventCache).forEach((bucket) => {
      if(!Array.isArray(bucket)) return;
      bucket.forEach((ev) => {
        if(eventIntersectsRange(ev, startDateStr, endDateStr)){
          results.push(ev);
        }
      });
    });
    return results;
  }

  // -------- Image attachment helpers --------
  function estimateDataUrlBytes(dataUrl){
    if(typeof dataUrl !== "string") return 0;
    const commaIdx = dataUrl.indexOf(",");
    const b64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl;
    return Math.ceil((b64.length * 3) / 4);
  }

  function clampDimensions(width, height, maxDim){
    if(width <= maxDim && height <= maxDim){
      return { width, height };
    }
    const scale = Math.min(maxDim / width, maxDim / height);
    return {
      width: Math.max(1, Math.round(width * scale)),
      height: Math.max(1, Math.round(height * scale))
    };
  }

  function readFileAsDataURL(file){
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  function loadImageElement(src){
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  async function compressImageFile(file){
    const base64 = await readFileAsDataURL(file);
    const img = await loadImageElement(base64);
    const { width, height } = clampDimensions(img.width, img.height, MAX_IMAGE_DIMENSION);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);

    let quality = 0.92;
    let dataUrl = canvas.toDataURL("image/jpeg", quality);
    let attempts = 0;
    while(estimateDataUrlBytes(dataUrl) > MAX_IMAGE_BYTES && attempts < 5){
      quality = Math.max(0.5, quality - 0.1);
      dataUrl = canvas.toDataURL("image/jpeg", quality);
      attempts += 1;
    }
    if(estimateDataUrlBytes(dataUrl) > MAX_IMAGE_BYTES){
      throw new Error("이미지 용량 초과");
    }
    return dataUrl;
  }

  async function handleImageFiles(fileList){
    if(!fileList) return;
    const files = Array.from(fileList);
    if(nlpImageAttachments.length >= MAX_IMAGE_ATTACHMENTS){
      showWarning(`이미지는 최대 ${MAX_IMAGE_ATTACHMENTS}장까지만 첨부할 수 있습니다.`);
      return;
    }
    for(const file of files){
      if(nlpImageAttachments.length >= MAX_IMAGE_ATTACHMENTS){
        showWarning(`이미지는 최대 ${MAX_IMAGE_ATTACHMENTS}장까지만 첨부할 수 있습니다.`);
        break;
      }
      if(!file.type.startsWith("image/")) continue;
      try{
        const dataUrl = await compressImageFile(file);
        nlpImageAttachments.push({
          id: imageAttachmentSeq++,
          name: file.name || `image-${imageAttachmentSeq}`,
          dataUrl
        });
      }catch(err){
        console.error(err);
        showWarning("이미지를 처리하지 못했습니다. 해상도를 줄이거나 다른 이미지를 사용해주세요.");
      }
    }
    renderNlpImageAttachments();
  }

  function renderNlpImageAttachments(){
    const host = document.getElementById("nlp-image-attachments");
    if(!host) return;
    host.innerHTML = "";
    nlpImageAttachments.forEach((att) => {
      const chip = document.createElement("div");
      chip.className = "image-chip";

      const img = document.createElement("img");
      img.src = att.dataUrl;
      img.alt = att.name || "attachment";
      chip.appendChild(img);

      const actions = document.createElement("div");
      actions.className = "image-chip-actions";

      const maskBtn = document.createElement("button");
      maskBtn.type = "button";
      maskBtn.textContent = "가리기";
      maskBtn.addEventListener("click", () => openImageEditor(att.id));
      actions.appendChild(maskBtn);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "삭제";
      removeBtn.addEventListener("click", () => removeImageAttachment(att.id));
      actions.appendChild(removeBtn);

      chip.appendChild(actions);
      host.appendChild(chip);
    });
  }

  function escapeNlpHtml(value){
    return (value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;");
  }

  function formatNlpInline(text){
    const safe = escapeNlpHtml(text);
    const withBold = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return withBold.replace(/\*(.+?)\*/g, "<em>$1</em>");
  }

  function renderNlpBubbleText(host, rawText){
    if(!host) return;
    host.innerHTML = "";
    const normalized = (rawText || "").toString().replace(/\\n/g, "\n");
    const lines = normalized.split(/\r?\n/);
    let listEl = null;

    const flushList = () => {
      if(listEl){
        host.appendChild(listEl);
        listEl = null;
      }
    };

    lines.forEach((line, idx) => {
      if(line.startsWith("- ")){
        if(!listEl){
          listEl = document.createElement("ul");
        }
        const li = document.createElement("li");
        li.innerHTML = formatNlpInline(line.slice(2));
        listEl.appendChild(li);
        return;
      }

      flushList();
      if(line === ""){
        host.appendChild(document.createElement("br"));
        return;
      }
      const span = document.createElement("span");
      span.innerHTML = formatNlpInline(line);
      host.appendChild(span);
      if(idx < lines.length - 1){
        host.appendChild(document.createElement("br"));
      }
    });

    flushList();
  }

  function renderNlpConversation(){
    const host = document.getElementById("nlp-chat");
    if(!host) return;
    host.innerHTML = "";
    nlpConversation.forEach((msg) => {
      const bubble = document.createElement("div");
      bubble.className = `nlp-msg ${msg.role}`;
      renderNlpBubbleText(bubble, msg.text);
      host.appendChild(bubble);
    });
  }

  function appendNlpMessage(role, text, options = {}){
    const value = (text || "").toString().trim();
    if(!value) return;
    const safeRole = role === "assistant" ? "assistant" : "user";
    const includeInPrompt = options.includeInPrompt !== false;
    nlpConversation.push({ role: safeRole, text: value, includeInPrompt });
    renderNlpConversation();
  }

  function buildNlpConversationText(){
    if(nlpConversation.length === 0){
      return "";
    }
    return nlpConversation
      .filter((msg) => msg.includeInPrompt !== false)
      .map((msg) => `${msg.role === "assistant" ? "assistant" : "사용자"}: ${msg.text}`)
      .join("\n");
  }

  function resetNlpConversation(){
    nlpConversation.length = 0;
    renderNlpConversation();
  }

  function removeImageAttachment(id){
    const idx = nlpImageAttachments.findIndex(att => att.id === id);
    if(idx >= 0){
      nlpImageAttachments.splice(idx, 1);
      renderNlpImageAttachments();
    }
  }

  function getNlpImagePayload(){
    return nlpImageAttachments.map(att => att.dataUrl);
  }

  function resetNlpComposerInputs(){
    const input = document.getElementById("nlp-unified-text");
    if(input){
      input.value = "";
      input.dispatchEvent(new Event("input"));
    }
    nlpImageAttachments.length = 0;
    renderNlpImageAttachments();
  }

  function resetRecurrenceEndSelections(){
    recurrenceEndSelections.clear();
  }

  function getRecurrenceEndSelection(idx){
    return recurrenceEndSelections.get(idx);
  }

  function setRecurrenceEndSelection(idx, payload){
    recurrenceEndSelections.set(idx, payload);
  }

  function buildRecurrenceEndControls(item, idx){
    const box = document.createElement("div");
    box.className = "rec-end-box";

    const title = document.createElement("h3");
    title.textContent = "반복 종료를 선택해주세요";
    box.appendChild(title);

    const options = document.createElement("div");
    options.className = "rec-end-options";
    const optionDefs = [
      { value:"none", label:"무기한" },
      { value:"until", label:"종료 날짜" },
      { value:"count", label:"횟수" }
    ];
    const name = `rec-end-${idx}`;

    const extra = document.createElement("div");
    extra.className = "rec-end-extra";

    const dateInput = document.createElement("input");
    dateInput.type = "date";
    dateInput.value = item.end_date || item.start_date || "";
    extra.appendChild(dateInput);

    const countInput = document.createElement("input");
    countInput.type = "number";
    countInput.min = "1";
    countInput.placeholder = "횟수";
    extra.appendChild(countInput);

    let currentMode = "none";

    const updateSelection = () => {
      if(currentMode === "until"){
        extra.style.display = "flex";
        dateInput.style.display = "";
        countInput.style.display = "none";
      }else if(currentMode === "count"){
        extra.style.display = "flex";
        dateInput.style.display = "none";
        countInput.style.display = "";
      }else{
        extra.style.display = "none";
        dateInput.style.display = "none";
        countInput.style.display = "none";
      }

      if(currentMode === "until"){
        setRecurrenceEndSelection(idx, {
          mode:"until",
          value: dateInput.value || null
        });
      }else if(currentMode === "count"){
        const numeric = countInput.value ? parseInt(countInput.value, 10) : null;
        setRecurrenceEndSelection(idx, {
          mode:"count",
          value: Number.isFinite(numeric) && numeric > 0 ? numeric : null
        });
      }else{
        setRecurrenceEndSelection(idx, { mode:"none", value:null });
      }
    };

    optionDefs.forEach(def => {
      const label = document.createElement("label");
      label.className = "rec-end-option";

      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = name;
      radio.value = def.value;
      if(def.value === "none"){
        radio.checked = true;
      }
      radio.addEventListener("change", () => {
        currentMode = def.value;
        updateSelection();
      });

      label.appendChild(radio);
      label.appendChild(document.createTextNode(def.label));
      options.appendChild(label);
    });

    dateInput.addEventListener("change", updateSelection);
    countInput.addEventListener("change", updateSelection);

    box.appendChild(options);
    box.appendChild(extra);
    currentMode = "none";
    updateSelection();
    return box;
  }

  function loadReasoningEffortFromStorage(){
    if(!IS_ADMIN) return;
    try{
      const stored = localStorage.getItem(REASONING_EFFORT_KEY);
      if(stored && ALLOWED_REASONING_EFFORTS.includes(stored)){
        reasoningEffortValue = stored;
      }
    }catch(_err){
      reasoningEffortValue = DEFAULT_REASONING_EFFORT;
    }
  }

  function saveReasoningEffortToStorage(value){
    if(!IS_ADMIN) return;
    try{
      localStorage.setItem(REASONING_EFFORT_KEY, value);
    }catch(_err){
      /* ignore */
    }
  }

  function initReasoningEffortControl(){
    const wrap = document.getElementById("reasoning-effort-control");
    const select = document.getElementById("reasoning-effort-select");
    if(!wrap || !select) return;
    if(!IS_ADMIN){
      wrap.style.display = "none";
      return;
    }
    loadReasoningEffortFromStorage();
    wrap.style.display = "flex";
    select.value = reasoningEffortValue;
    select.addEventListener("change", () => {
      const val = select.value;
      if(ALLOWED_REASONING_EFFORTS.includes(val)){
        reasoningEffortValue = val;
        saveReasoningEffortToStorage(val);
      }else{
        select.value = reasoningEffortValue;
      }
    });
  }

  function getReasoningEffortSetting(){
    if(!IS_ADMIN) return null;
    const select = document.getElementById("reasoning-effort-select");
    const candidate = select ? select.value : reasoningEffortValue;
    return ALLOWED_REASONING_EFFORTS.includes(candidate) ? candidate : null;
  }

  function openImageEditor(attachmentId){
    const target = nlpImageAttachments.find(att => att.id === attachmentId);
    if(!target || !imageEditorOverlay || !imageEditorCanvas || !imageEditorCtx){
      return;
    }
    imageEditorState.attachmentId = attachmentId;
    imageEditorState.drawing = false;
    imageEditorState.pointerId = null;
    setEditorCanvasFromDataUrl(target.dataUrl, true).then(() => {
      imageEditorOverlay.classList.add("active");
    }).catch(() => {
      showWarning("이미지 편집 도중 오류가 발생했습니다.");
      imageEditorState.attachmentId = null;
    });
  }

  function closeImageEditor(){
    if(imageEditorOverlay){
      imageEditorOverlay.classList.remove("active");
    }
    imageEditorState.attachmentId = null;
    imageEditorState.drawing = false;
    imageEditorState.pointerId = null;
    if(imageEditorSelectionCtx && imageEditorSelection){
      imageEditorSelectionCtx.clearRect(0, 0, imageEditorSelection.width, imageEditorSelection.height);
    }
  }

  function setEditorCanvasFromDataUrl(dataUrl, resetUndo){
    if(!imageEditorCanvas || !imageEditorCtx) return Promise.reject(new Error("canvas missing"));
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const { width, height } = clampDimensions(img.width, img.height, MAX_IMAGE_DIMENSION);
        imageEditorCanvas.width = width;
        imageEditorCanvas.height = height;
        if(imageEditorSelection){
          imageEditorSelection.width = width;
          imageEditorSelection.height = height;
        }
        imageEditorCtx.clearRect(0, 0, width, height);
        imageEditorCtx.drawImage(img, 0, 0, width, height);
        if(imageEditorSelectionCtx){
          imageEditorSelectionCtx.clearRect(0, 0, width, height);
        }
        if(resetUndo){
          imageEditorUndoStack = [imageEditorCanvas.toDataURL("image/png")];
        }
        resolve();
      };
      img.onerror = reject;
      img.src = dataUrl;
    });
  }

  function pushEditorSnapshot(){
    if(!imageEditorCanvas) return;
    const snapshot = imageEditorCanvas.toDataURL("image/png");
    imageEditorUndoStack.push(snapshot);
    if(imageEditorUndoStack.length > 10){
      imageEditorUndoStack.shift();
    }
  }

  function undoImageEditor(){
    if(imageEditorUndoStack.length <= 1) return;
    imageEditorUndoStack.pop();
    const previous = imageEditorUndoStack[imageEditorUndoStack.length - 1];
    setEditorCanvasFromDataUrl(previous, false);
  }

  function getEditorPointerPosition(evt){
    if(!imageEditorSelection) return { x: 0, y: 0 };
    const rect = imageEditorSelection.getBoundingClientRect();
    const scaleX = imageEditorSelection.width / rect.width;
    const scaleY = imageEditorSelection.height / rect.height;
    return {
      x: (evt.clientX - rect.left) * scaleX,
      y: (evt.clientY - rect.top) * scaleY
    };
  }

  function clearSelectionOverlay(){
    if(imageEditorSelectionCtx && imageEditorSelection){
      imageEditorSelectionCtx.clearRect(0, 0, imageEditorSelection.width, imageEditorSelection.height);
    }
  }

  function drawSelectionPreview(x1, y1, x2, y2){
    if(!imageEditorSelectionCtx) return;
    clearSelectionOverlay();
    const x = Math.min(x1, x2);
    const y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1);
    const h = Math.abs(y2 - y1);
    if(w < 4 || h < 4) return;
    imageEditorSelectionCtx.fillStyle = "rgba(0,0,0,0.35)";
    imageEditorSelectionCtx.fillRect(x, y, w, h);
    imageEditorSelectionCtx.strokeStyle = "rgba(0,0,0,0.8)";
    imageEditorSelectionCtx.lineWidth = 2;
    imageEditorSelectionCtx.strokeRect(x + 1, y + 1, Math.max(0, w - 2), Math.max(0, h - 2));
  }

  function commitBlackout(x1, y1, x2, y2){
    if(!imageEditorCanvas || !imageEditorCtx) return;
    const w = Math.abs(x2 - x1);
    const h = Math.abs(y2 - y1);
    if(w < 4 || h < 4) return;
    const x = Math.max(0, Math.min(x1, x2));
    const y = Math.max(0, Math.min(y1, y2));
    imageEditorCtx.fillStyle = "#000000";
    imageEditorCtx.globalAlpha = 1;
    imageEditorCtx.fillRect(x, y, w, h);
    pushEditorSnapshot();
  }

  function imageEditorPointerDown(evt){
    if(!imageEditorSelection) return;
    evt.preventDefault();
    const { x, y } = getEditorPointerPosition(evt);
    imageEditorState.drawing = true;
    imageEditorState.pointerId = evt.pointerId;
    imageEditorState.startX = x;
    imageEditorState.startY = y;
    imageEditorSelection.setPointerCapture?.(evt.pointerId);
  }

  function imageEditorPointerMove(evt){
    if(!imageEditorState.drawing || imageEditorState.pointerId !== evt.pointerId) return;
    evt.preventDefault();
    const { x, y } = getEditorPointerPosition(evt);
    drawSelectionPreview(imageEditorState.startX, imageEditorState.startY, x, y);
  }

  function imageEditorPointerUp(evt){
    if(!imageEditorState.drawing || imageEditorState.pointerId !== evt.pointerId) return;
    evt.preventDefault();
    const { x, y } = getEditorPointerPosition(evt);
    commitBlackout(imageEditorState.startX, imageEditorState.startY, x, y);
    clearSelectionOverlay();
    imageEditorState.drawing = false;
    imageEditorState.pointerId = null;
    imageEditorSelection?.releasePointerCapture?.(evt.pointerId);
  }

  function imageEditorPointerCancel(evt){
    if(imageEditorState.pointerId !== evt.pointerId) return;
    clearSelectionOverlay();
    imageEditorState.drawing = false;
    imageEditorState.pointerId = null;
    imageEditorSelection?.releasePointerCapture?.(evt.pointerId);
  }

  function applyImageEditorEdits(){
    if(imageEditorState.attachmentId == null || !imageEditorCanvas){
      closeImageEditor();
      return;
    }
    let quality = 0.9;
    let dataUrl = imageEditorCanvas.toDataURL("image/jpeg", quality);
    let attempts = 0;
    while(estimateDataUrlBytes(dataUrl) > MAX_IMAGE_BYTES && attempts < 4){
      quality = Math.max(0.5, quality - 0.1);
      dataUrl = imageEditorCanvas.toDataURL("image/jpeg", quality);
      attempts += 1;
    }
    if(estimateDataUrlBytes(dataUrl) > MAX_IMAGE_BYTES){
      showWarning("편집 결과 이미지가 너무 큽니다. 가린 영역을 줄이거나 이미지를 축소해주세요.");
      return;
    }
    const target = nlpImageAttachments.find(att => att.id === imageEditorState.attachmentId);
    if(target){
      target.dataUrl = dataUrl;
      renderNlpImageAttachments();
    }
    closeImageEditor();
  }

  function setupImageComposer(){
    const attachBtn = document.getElementById("nlp-attach-btn");
    const fileInput = document.getElementById("nlp-image-input");
    imageEditorOverlay = document.getElementById("image-editor-overlay");
    imageEditorCanvas = document.getElementById("image-editor-canvas");
    imageEditorCtx = imageEditorCanvas?.getContext("2d") || null;
    imageEditorSelection = document.getElementById("image-editor-selection");
    imageEditorSelectionCtx = imageEditorSelection?.getContext("2d") || null;

    attachBtn?.addEventListener("click", () => fileInput?.click());
    fileInput?.addEventListener("change", async (event) => {
      await handleImageFiles(event.target.files);
      event.target.value = "";
    });

    document.getElementById("image-editor-cancel")?.addEventListener("click", closeImageEditor);
    document.getElementById("image-editor-apply")?.addEventListener("click", applyImageEditorEdits);
    document.getElementById("image-editor-undo")?.addEventListener("click", undoImageEditor);
    imageEditorOverlay?.addEventListener("click", (event) => {
      if(event.target === imageEditorOverlay){
        closeImageEditor();
      }
    });

    imageEditorSelection?.addEventListener("pointerdown", imageEditorPointerDown, { passive: false });
    imageEditorSelection?.addEventListener("pointermove", imageEditorPointerMove, { passive: false });
    imageEditorSelection?.addEventListener("pointerup", imageEditorPointerUp, { passive: false });
    imageEditorSelection?.addEventListener("pointercancel", imageEditorPointerCancel, { passive: false });
    imageEditorSelection?.addEventListener("pointerleave", imageEditorPointerCancel, { passive: false });

    renderNlpImageAttachments();
  }

  function setupEventModalControls(){
    const overlay = document.getElementById("event-modal-overlay");
    if(!overlay) return;
    document.getElementById("event-modal-close")?.addEventListener("click", closeEventModal);
    document.getElementById("event-modal-cancel")?.addEventListener("click", closeEventModal);
    document.getElementById("event-modal-save")?.addEventListener("click", saveEventModal);
    document.getElementById("event-modal-delete")?.addEventListener("click", deleteEventModal);
    overlay.addEventListener("click", (event) => {
      if(event.target === overlay){
        closeEventModal();
      }
    });
  }

  // -------- Confirm Modal helpers --------
  function openConfirm(){ document.getElementById("confirm-overlay").style.display = "flex"; }
  function closeConfirm(){
    document.getElementById("confirm-overlay").style.display = "none";
    document.getElementById("confirm-list").innerHTML = "";
    confirmState = { mode: null, addItems: [], deleteGroups: [] };
    resetRecurrenceEndSelections();
  }

  async function openAddConfirm(text, imagePayload = []){
    appendNlpMessage("user", text);
    const payloadText = buildNlpConversationText() || text;
    const payload = { text: payloadText };
    if(Array.isArray(imagePayload) && imagePayload.length){
      payload.images = imagePayload;
    }
    const effort = getReasoningEffortSetting();
    if(effort){
      payload.reasoning_effort = effort;
    }
    const res = await fetch(apiBase + "/nlp-preview", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });
    if(!res.ok){
      showWarning("추가할 일정을 해석하지 못했습니다.");
      return;
    }
    const data = await res.json();
    if(data && data.context_used){
      appendNlpMessage("assistant", "기존 일정 분석 중", { includeInPrompt: false });
      appendNlpMessage("assistant", "기존 일정 분석 완료", { includeInPrompt: false });
    }
    if(data && data.need_more_information){
      const question = (data.content || "").trim();
      if(question){
        appendNlpMessage("assistant", question);
      }else{
        showWarning("추가로 확인할 정보가 필요합니다.");
      }
      const input = document.getElementById("nlp-unified-text");
      if(input){
        input.value = "";
        input.dispatchEvent(new Event("input"));
      }
      return;
    }
    const items = Array.isArray(data?.items) ? data.items : [];
    if(items.length === 0){
      showWarning("추가할 일정을 찾지 못했습니다.");
      return;
    }

    confirmState.mode = "add";
    confirmState.addItems = items;
    resetRecurrenceEndSelections();

    document.getElementById("confirm-title").textContent = "이 일정을 추가할까요?";
    document.getElementById("confirm-desc").textContent = "체크한 항목만 추가됩니다. 반복 일정은 묶어서 선택하거나 상세에서 일부만 고를 수 있습니다.";

    const host = document.getElementById("confirm-list");
    host.innerHTML = "";

    const createEditableLabel = (initial, placeholder, onCommit) => {
      let currentValue = initial || "";
      const wrapper = document.createElement("div");
      wrapper.className = "cm-editable";
      let label = document.createElement("span");
      label.textContent = currentValue || placeholder;
      const iconBtn = document.createElement("button");
      iconBtn.type = "button";
      iconBtn.className = "cm-edit-icon";
      iconBtn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zm2.92.83h-.67v-.67l8.5-8.5.67.67-8.5 8.5zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>`;

      const applyLabel = (value) => {
        currentValue = value || "";
        if(label) label.textContent = currentValue || placeholder;
      };

      const enterEdit = () => {
        if(wrapper.classList.contains("editing")) return;
        wrapper.classList.add("editing");
        const input = document.createElement("input");
        input.type = "text";
        input.value = currentValue || "";
        wrapper.insertBefore(input, iconBtn);
        if(label) label.remove();
        iconBtn.style.visibility = "hidden";
        input.focus();

        const finish = (apply) => {
          if(apply){
            const nextValue = input.value.trim();
            currentValue = nextValue;
            onCommit(nextValue);
          }
          input.remove();
          label = document.createElement("span");
          label.textContent = currentValue || placeholder;
          wrapper.insertBefore(label, iconBtn);
          iconBtn.style.visibility = "";
          wrapper.classList.remove("editing");
        };

        input.addEventListener("blur", () => finish(true));
        input.addEventListener("keydown", (e) => {
          if(e.key === "Enter"){
            e.preventDefault();
            finish(true);
          }else if(e.key === "Escape"){
            e.preventDefault();
            finish(false);
          }
        });
      };

      wrapper.appendChild(label);
      wrapper.appendChild(iconBtn);
      wrapper.addEventListener("click", enterEdit);
      iconBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        enterEdit();
      });

      return {
        wrapper,
        updateLabel: applyLabel
      };
    };

    items.forEach((it, idx) => {
      const row = document.createElement("div");
      row.className = "cm-row";

      const top = document.createElement("div");
      top.className = "cm-row-top";

      const left = document.createElement("div");
      left.className = "cm-left";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      cb.className = "cm-check";
      cb.dataset.addIndex = String(idx);
      cb.dataset.role = "add-top";

      const main = document.createElement("div");
      main.className = "cm-main";

      const line1 = document.createElement("div");
      line1.className = "cm-line1";

      const line2 = document.createElement("div");
      line2.className = "cm-line2";

      const locationLine = document.createElement("div");
      locationLine.className = "cm-line-location";

      if(it.type === "single"){
        const titleEditable = createEditableLabel(it.title || "", "(제목 없음)", (val) => {
          it.title = val || "";
          updateSingleMeta();
        });
        const locationEditable = createEditableLabel(it.location || "", "(장소 없음)", (val) => {
          it.location = (val && val.trim()) || null;
          updateSingleMeta();
        });

        const updateSingleMeta = () => {
          titleEditable.updateLabel(it.title || "");
          locationEditable.updateLabel(it.location || "");
          line2.textContent = fmtRange(it.start, it.end, it.all_day);
        };
        updateSingleMeta();

        line1.appendChild(titleEditable.wrapper);
        locationLine.appendChild(locationEditable.wrapper);
        main.appendChild(line1);
        main.appendChild(locationLine);
        main.appendChild(line2);
        left.appendChild(cb);
        left.appendChild(main);
        top.appendChild(left);
        row.appendChild(top);
        host.appendChild(row);
        return;
      }

      const sd = it.start_date || "?";
      const ed = it.end_date || "?";
      const cnt = (typeof it.count === "number") ? it.count : 0;
      const time = it.time ? it.time : "시간 없음";

      const recurTitleEditable = createEditableLabel(it.title || "", "(제목 없음)", (val) => {
        it.title = val || "";
        updateRecurringMeta();
      });
      const recurLocationEditable = createEditableLabel(it.location || "", "(장소 없음)", (val) => {
        it.location = (val && val.trim()) || null;
        updateRecurringMeta();
      });

      const updateRecurringMeta = () => {
        recurTitleEditable.updateLabel(it.title || "");
        recurLocationEditable.updateLabel(it.location || "");
        line2.textContent = `반복: ${sd}~${ed} · ${time} · ${cnt}회`;
      };
      updateRecurringMeta();

      line1.appendChild(recurTitleEditable.wrapper);
      locationLine.appendChild(recurLocationEditable.wrapper);

      main.appendChild(line1);
      main.appendChild(locationLine);
      main.appendChild(line2);

      if(Array.isArray(it.samples) && it.samples.length){
        const mini = document.createElement("div");
        mini.className = "cm-mini";
        const s = it.samples.slice(0,3).map(x => (x || "").replace("T"," ")).join(" / ");
        mini.textContent = `예: ${s}${(cnt > 3) ? " …" : ""}`;
        main.appendChild(mini);
      }

      left.appendChild(cb);
      left.appendChild(main);

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "cm-toggle";
      toggle.textContent = "상세";

      const sub = document.createElement("div");
      sub.className = "cm-sublist";

      const occ = Array.isArray(it.occurrences) ? it.occurrences : [];
      occ.forEach((occurrence, occIdx) => {
        const subItem = document.createElement("div");
        subItem.className = "cm-subitem";

        const occCb = document.createElement("input");
        occCb.type = "checkbox";
        occCb.checked = true;
        occCb.dataset.role = "add-occurrence";
        occCb.dataset.addIndex = String(idx);
        occCb.dataset.addOccurrenceIndex = String(occIdx);

        const info = document.createElement("div");
        info.style.minWidth = "0";

        const oLine1 = document.createElement("div");
        oLine1.className = "cm-line1";
        oLine1.textContent = occurrence.title || it.title || "";

        const oLine2 = document.createElement("div");
        oLine2.className = "cm-line2";
        oLine2.textContent = fmtRange(occurrence.start, occurrence.end, occurrence.all_day) + (occurrence.location ? ` · ${occurrence.location}` : "");

        info.appendChild(oLine1);
        info.appendChild(oLine2);

        subItem.appendChild(occCb);
        subItem.appendChild(info);
        sub.appendChild(subItem);
      });

      cb.addEventListener("change", () => {
        cb.indeterminate = false;
        sub.querySelectorAll("input[type=checkbox][data-role='add-occurrence']").forEach(x => {
          x.checked = cb.checked;
        });
      });

      sub.addEventListener("change", () => {
        const cbs = Array.from(sub.querySelectorAll("input[type=checkbox][data-role='add-occurrence']"));
        const all = cbs.every(x => x.checked);
        const any = cbs.some(x => x.checked);
        cb.checked = any;
        cb.indeterminate = any && !all;
      });

      toggle.addEventListener("click", () => {
        const open = sub.style.display === "block";
        sub.style.display = open ? "none" : "block";
        toggle.textContent = open ? "상세" : "접기";
      });

      top.appendChild(left);
      top.appendChild(toggle);
      row.appendChild(top);
      row.appendChild(sub);
      if(it.requires_end_confirmation){
        const endControls = buildRecurrenceEndControls(it, idx);
        row.appendChild(endControls);
      }
      host.appendChild(row);
    });

    openConfirm();
  }

  async function openDeleteConfirm(text, scope){
    if(!scope) return;
    const payload = { text, start_date: scope.start, end_date: scope.end };
    const effort = getReasoningEffortSetting();
    if(effort){
      payload.reasoning_effort = effort;
    }
    const res = await fetch(apiBase + "/nlp-delete-preview", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });
    if(!res.ok){
      showWarning("삭제할 일정을 찾지 못했습니다.");
      return;
    }
    const data = await res.json();
    const groups = Array.isArray(data?.groups) ? data.groups : [];
    if(groups.length === 0){
      showWarning("삭제할 일정을 찾지 못했습니다.");
      return;
    }

    confirmState.mode = "delete";
    confirmState.deleteGroups = groups;

    document.getElementById("confirm-title").textContent = "이 일정을 삭제할까요?";
    document.getElementById("confirm-desc").textContent = "체크한 항목만 삭제됩니다. 반복 일정은 묶어서 선택할 수 있습니다.";

    const host = document.getElementById("confirm-list");
    host.innerHTML = "";

    groups.forEach((g, gi) => {
      const row = document.createElement("div");
      row.className = "cm-row";

      const top = document.createElement("div");
      top.className = "cm-row-top";

      const left = document.createElement("div");
      left.className = "cm-left";

      const gcb = document.createElement("input");
      gcb.type = "checkbox";
      gcb.checked = true;
      gcb.className = "cm-check";
      gcb.dataset.groupIndex = String(gi);

      const main = document.createElement("div");
      main.className = "cm-main";

      const line1 = document.createElement("div");
      line1.className = "cm-line1";
      const kindLabel = (g.kind === "recurring") ? "반복" : "단일";
      line1.textContent = `${kindLabel} · ${g.title || ""}`;

      const line2 = document.createElement("div");
      line2.className = "cm-line2";
      const time = g.time ? g.time : "";
      const loc = g.location ? g.location : "";
      const cnt = (typeof g.count === "number") ? g.count : (Array.isArray(g.ids) ? g.ids.length : 0);
      line2.textContent = `${time}${time && loc ? " · " : ""}${loc}${(time || loc) ? " · " : ""}${cnt}개`;

      main.appendChild(line1);
      main.appendChild(line2);

      left.appendChild(gcb);
      left.appendChild(main);

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "cm-toggle";
      toggle.textContent = "상세";

      const sub = document.createElement("div");
      sub.className = "cm-sublist";

      const items = Array.isArray(g.items) ? g.items : [];
      items.forEach((it) => {
        const si = document.createElement("div");
        si.className = "cm-subitem";

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.deleteId = String(it.id);

        const meta = document.createElement("div");
        meta.style.minWidth = "0";

        const l1 = document.createElement("div");
        l1.className = "cm-line1";
        l1.textContent = it.title || "";

        const l2 = document.createElement("div");
        l2.className = "cm-line2";
        l2.textContent = fmtRange(it.start, it.end, it.all_day) + (it.location ? ` · ${it.location}` : "");

        meta.appendChild(l1);
        meta.appendChild(l2);

        si.appendChild(cb);
        si.appendChild(meta);
        sub.appendChild(si);
      });

      gcb.addEventListener("change", () => {
        sub.querySelectorAll("input[type=checkbox][data-delete-id]").forEach(x => {
          x.checked = gcb.checked;
          x.indeterminate = false;
        });
      });

      sub.addEventListener("change", () => {
        const cbs = Array.from(sub.querySelectorAll("input[type=checkbox][data-delete-id]"));
        const all = cbs.every(x => x.checked);
        const any = cbs.some(x => x.checked);
        gcb.checked = any;
        gcb.indeterminate = any && !all;
      });

      toggle.addEventListener("click", () => {
        const open = sub.style.display === "block";
        sub.style.display = open ? "none" : "block";
        toggle.textContent = open ? "상세" : "접기";
      });

      top.appendChild(left);
      top.appendChild(toggle);

      row.appendChild(top);
      row.appendChild(sub);
      host.appendChild(row);
    });

    openConfirm();
  }

  // ✅ 실행 버튼/Enter: 바로 추가/삭제 X → 확인 모달 오픈
  async function runUnifiedNlpAction(){
    const input = document.getElementById("nlp-unified-text");
    const toggle = document.getElementById("nlp-mode-toggle");
    const text = input?.value?.trim() ?? "";
    const imagePayload = getNlpImagePayload();

    const isDelete = !!toggle?.checked;
    if(isDelete){
      if(!text){
        showWarning("삭제할 문장을 입력해주세요.");
        return;
      }
    }else if(!text && imagePayload.length === 0){
      showWarning("문장이나 이미지를 입력해주세요.");
      return;
    }

    setUnifiedBusy(true);
    try{
      if(isDelete){
        const scope = getDeleteScopeOrAlert();
        if(!scope) return;
        await openDeleteConfirm(text, scope);
      }else{
        await openAddConfirm(text, imagePayload);
      }
    }catch(err){
      console.error(err);
      showWarning("실행 중 오류가 발생했습니다. 다시 시도해주세요.");
    }finally{
      setUnifiedBusy(false);
    }
  }

  const onDomReady = () => {
    if(window.__CALENDAR_APP_INIT__) return;
    window.__CALENDAR_APP_INIT__ = true;
    // Calendar init
    let calendarFadeTimer = null;
    const calendarContent = document.querySelector("#calendar-container .calendar-main-content");
    const calendarEl = document.getElementById("calendar");
    selectedDateStr = toDateStrLocal(new Date());
    setSelectedDate(selectedDateStr);
    initEventsPanelDock();
    requestAnimationFrame(updateViewSwitchIndicators);

    calendar = new FullCalendar.Calendar(calendarEl, {
      initialView:"dayGridMonth",
      locale:"ko",
      height:"auto",
      headerToolbar:false,
      fixedWeekCount:false,
      dayMaxEventRows:4,
      displayEventTime:false,

      dayCellClassNames: (arg) => {
        const ds = toDateStrLocal(arg.date);
        return (ds === selectedDateStr) ? ["selected-day"] : [];
      },
      dayCellContent: (arg) => String(arg.date.getDate()),
      dayHeaderClassNames: (arg) => {
        const ds = toDateStrLocal(arg.date);
        return (ds === selectedDateStr) ? ["selected-day-header"] : [];
      },

      events: async (info, success, failure) => {
        const formatLocalEvent = (ev) => {
          const rawStart = ev.start || "";
          const rawEnd = ev.end || null;
          const allDay = (ev.all_day === true) || isAllDayRange(rawStart, rawEnd);
          if(allDay){
            const startDateOnly = toDateOnly(rawStart) || toDateOnly(rawEnd) || "";
            const inclusiveEnd = toDateOnly(rawEnd) || startDateOnly;
            const exclusiveEnd = addDaysToDateStr(inclusiveEnd, 1) || addDaysToDateStr(startDateOnly, 1);
            const startValue = startDateOnly || toDateStrLocal(new Date());
            return {
              id:String(ev.id),
              title:ev.title,
              start:startValue,
              end:exclusiveEnd,
              allDay:true,
              extendedProps:{location: ev.location || "", allDay:true, source:"local"}
            };
          }
          return {
            id:String(ev.id),
            title:ev.title,
            start:rawStart,
            end:rawEnd || null,
            allDay:false,
            extendedProps:{location: ev.location || "", allDay:false, source:"local"}
          };
        };

        const formatGoogleEvent = (ev) => {
          const rawStart = ev.start || "";
          const rawEnd = ev.end || null;
          const allDay = !!ev.all_day;
          if(allDay){
            const startDateOnly = toDateOnly(rawStart) || toDateOnly(rawEnd) || "";
            const inclusiveEnd = toDateOnly(rawEnd) || startDateOnly;
            const exclusiveEnd = addDaysToDateStr(inclusiveEnd, 1) || addDaysToDateStr(startDateOnly, 1);
            return {
              id:ev.id,
              title: ev.title || "(제목 없음)",
              start:startDateOnly || rawStart,
              end:exclusiveEnd,
              allDay:true,
              extendedProps:{
                location: ev.location || "",
                allDay:true,
                source:"google",
                googleId: ev.google_event_id || ""
              }
            };
          }
          return {
            id:ev.id,
            title: ev.title || "(제목 없음)",
            start:rawStart,
            end:rawEnd || null,
            allDay:false,
            extendedProps:{
              location: ev.location || "",
              allDay:false,
              source:"google",
              googleId: ev.google_event_id || ""
            }
          };
        };

        try{
          const showLocal = !IS_GOOGLE_MODE;
          const showGoogle = IS_GOOGLE_MODE;
          const viewRange = getCalendarViewRange(info);
          const viewStartDate = viewRange.startDate;
          const viewEndDate = viewRange.endDate;
          const viewStartStr = viewRange.startStr;
          const viewEndStr = viewRange.endStr;

          let localEvents = [];
          if(showLocal){
            const data = await getLocalEventsForRange(viewStartStr, viewEndStr);
            localEvents = Array.isArray(data) ? data.map(formatLocalEvent) : [];
          }

          let googleEvents = [];
          if(showGoogle){
            const years = [];
            const startYear = viewStartDate.getFullYear();
            const endYear = viewEndDate.getFullYear();
            for(let y = startYear; y <= endYear; y += 1){
              years.push(y);
            }
            await ensureGoogleEventsForYears(years);
            const cachedRange = collectGoogleEventsBetween(viewStartStr, viewEndStr);
            googleEvents = cachedRange.map(formatGoogleEvent);
          }

          success([...(localEvents || []), ...(googleEvents || [])]);
        }catch(err){
          console.error(err);
          failure(err);
        }
      },

      dateClick: (info) => {
        setSelectedDate(info.dateStr);
        loadEventListForDate(selectedDateStr);
      },

      eventClick: (info) => {
        const ev = info.event;
        openEventModal({
          id: ev.id,
          title: ev.title,
          start: ev.start ? ev.start.toISOString() : "",
          end: ev.end ? ev.end.toISOString() : "",
          location: ev.extendedProps.location,
          notes: "",
          source: ev.extendedProps.source || "local",
          googleId: ev.extendedProps.googleId || null,
          allDay: ev.allDay
        });
      },

      datesSet: (info) => {
        updateYearMonthLabel(info.view.calendar.getDate());
        setActiveView(info.view.type);
        syncSelectedDayHighlight();
        if(calendarContent){
          window.clearTimeout(calendarFadeTimer);
          calendarFadeTimer = window.setTimeout(() => {
            calendarContent.classList.remove("is-fading");
          }, 40);
        }
      },

      loading: (isLoading) => {
        if(!isLoading && !initialListLoaded){
          initialListLoaded = true;
          if(selectedDateStr){
            loadEventListForDate(selectedDateStr);
          }
        }
      }
    });

    calendar.render();
    syncSelectedDayHighlight();
    updateYearMonthLabel(calendar.getDate());
    setActiveView(calendar.view.type);

    calendarEl.addEventListener("dblclick", (event) => {
      const target = event.target;
      if(target.closest(".fc-event")) return;
      const dayCell = target.closest(".fc-daygrid-day");
      const timeCol = target.closest(".fc-timegrid-col");
      const dateStr = (dayCell && dayCell.getAttribute("data-date"))
        || (timeCol && timeCol.getAttribute("data-date"));
      if(!dateStr) return;
      setSelectedDate(dateStr);
      loadEventListForDate(dateStr);
      openCreateEventModal(dateStr);
    });

    const runCalendarFadeNav = (action) => {
      if(!calendarContent){
        action();
        return;
      }
      calendarContent.classList.add("is-fading");
      window.clearTimeout(calendarFadeTimer);
      calendarFadeTimer = window.setTimeout(() => {
        action();
        calendarFadeTimer = window.setTimeout(() => {
          calendarContent.classList.remove("is-fading");
        }, 40);
      }, 40);
    };

    // topbar controls
    yearViewYear = calendar.getDate().getFullYear();
    const isYearViewActive = () => document.getElementById("year-view")?.classList.contains("active");

    document.getElementById("cal-prev").addEventListener("click", () => {
      if(isYearViewActive()){
        refreshYearView(yearViewYear - 1);
        return;
      }
      runCalendarFadeNav(() => calendar.prev());
    });
    document.getElementById("cal-next").addEventListener("click", () => {
      if(isYearViewActive()){
        refreshYearView(yearViewYear + 1);
        return;
      }
      runCalendarFadeNav(() => calendar.next());
    });
    document.getElementById("cal-today").addEventListener("click", () => {
      if(isYearViewActive()){
        refreshYearView(new Date().getFullYear());
        return;
      }
      runCalendarFadeNav(() => {
        calendar.today();
        const d = toDateStrLocal(new Date());
        setSelectedDate(d);
        loadEventListForDate(d);
      });
    });

    const eventSearchWrap = document.querySelector(".event-search");
    const eventSearchInput = document.getElementById("event-search-input");
    const eventSearchRangeBack = document.getElementById("event-search-range-back");
    const eventSearchRangeForward = document.getElementById("event-search-range-forward");
    const eventSearchSettingsBtn = document.getElementById("event-search-settings-btn");
    const eventSearchRangeClose = document.getElementById("event-search-range-close");
    const eventSearchRangeApply = document.getElementById("event-search-range-apply");
    const eventSearchBtn = document.getElementById("event-search-btn");
    const setEventSearchOpen = (isOpen) => {
      if(!eventSearchWrap) return;
      eventSearchWrap.classList.toggle("is-open", !!isOpen);
    };
    const setRangeOpen = (isOpen) => {
      if(!eventSearchWrap) return;
      eventSearchWrap.classList.toggle("is-range-open", !!isOpen);
      if(isOpen){
        setEventSearchOpen(true);
      }
    };
    const readRangeValue = (input) => {
      if(!input) return 0;
      const raw = (input.value || "").trim();
      if(!raw) return 0;
      const num = parseInt(raw, 10);
      return Number.isFinite(num) ? num : 0;
    };
    const validateRangeInputs = () => {
      const backYears = readRangeValue(eventSearchRangeBack);
      const forwardYears = readRangeValue(eventSearchRangeForward);
      if(backYears < 0 || forwardYears < 0){
        showWarning("검색 범위는 0년 이상으로 입력해주세요.");
        return false;
      }
      if(backYears + forwardYears > 10){
        showWarning("검색 범위는 이전/이후 합산 10년까지 설정할 수 있습니다.");
        return false;
      }
      return true;
    };
    const runEventSearch = async () => {
      const query = normalizeSearchQuery(eventSearchInput?.value);
      if(!query){
        showWarning("검색어를 입력해주세요.");
        return;
      }
      if(!validateRangeInputs()){
        return;
      }
      const backYears = readRangeValue(eventSearchRangeBack);
      const forwardYears = readRangeValue(eventSearchRangeForward);
      const range = getCustomSearchRange(backYears, forwardYears);
      let candidates = [];
      try{
        if(IS_GOOGLE_MODE){
          const baseYear = calendar.getDate().getFullYear();
          let years = [];
          if(range && range.start && range.end){
            years = getYearsFromRange(range.start, range.end);
          }else{
            years = extractSearchYears(query, baseYear);
            if(years.length === 0 && Number.isFinite(baseYear)){
              years.push(baseYear);
            }
          }
          if(years.length === 0 && Number.isFinite(baseYear)){
            years.push(baseYear);
          }
          await ensureGoogleEventsForYears(years);
          candidates = years.flatMap((year) => (Array.isArray(googleEventCache[year]) ? googleEventCache[year] : []));
          if(range && range.start && range.end){
            candidates = candidates.filter((ev) => eventIntersectsRange(ev, range.start, range.end));
          }
        }else{
          if(range && range.start && range.end){
            candidates = await fetchLocalEventsBetween(range.start, range.end);
          }else{
            candidates = await fetchLocalEventsBetween(null, null);
          }
        }
      }catch(err){
        console.error(err);
        showWarning("검색 중 오류가 발생했습니다.");
        return;
      }
      const matches = candidates.filter((ev) => eventMatchesQuery(ev, query));
      if(matches.length === 0){
        showWarning("일정을 찾지 못했습니다.");
        return;
      }
      const best = pickBestMatch(matches);
      const targetDate = getEventDateStr(best);
      if(!targetDate){
        showWarning("이 일정의 날짜를 찾지 못했습니다.");
        return;
      }
      calendar.gotoDate(targetDate);
      setSelectedDate(targetDate);
      loadEventListForDate(targetDate);
    };
    if(eventSearchBtn){
      eventSearchBtn.addEventListener("click", () => {
        if(eventSearchWrap && !eventSearchWrap.classList.contains("is-open")){
          setEventSearchOpen(true);
          eventSearchInput?.focus();
          return;
        }
        runEventSearch();
      });
    }
    if(eventSearchSettingsBtn){
      eventSearchSettingsBtn.addEventListener("click", (e) => {
        e.preventDefault();
        setRangeOpen(!eventSearchWrap?.classList.contains("is-range-open"));
      });
    }
    if(eventSearchRangeClose){
      eventSearchRangeClose.addEventListener("click", () => {
        setRangeOpen(false);
      });
    }
    if(eventSearchRangeApply){
      eventSearchRangeApply.addEventListener("click", () => {
        if(!validateRangeInputs()){
          return;
        }
        setRangeOpen(false);
      });
    }
    if(eventSearchInput){
      eventSearchInput.addEventListener("focus", () => {
        setEventSearchOpen(true);
      });
      eventSearchInput.addEventListener("keydown", (e) => {
        if(e.key === "Enter"){
          e.preventDefault();
          runEventSearch();
        }else if(e.key === "Escape"){
          setRangeOpen(false);
          setEventSearchOpen(false);
          eventSearchInput.blur();
        }
      });
      eventSearchInput.addEventListener("blur", () => {
        setTimeout(() => {
          if(eventSearchWrap && document.activeElement && eventSearchWrap.contains(document.activeElement)){
            return;
          }
          if(!normalizeSearchQuery(eventSearchInput.value) && !eventSearchWrap?.classList.contains("is-range-open")){
            setEventSearchOpen(false);
          }
        }, 0);
      });
    }
    [eventSearchRangeBack, eventSearchRangeForward].forEach((input) => {
      if(!input) return;
      input.addEventListener("focus", () => {
        setEventSearchOpen(true);
      });
      input.addEventListener("blur", () => {
        validateRangeInputs();
      });
    });
    document.addEventListener("click", (event) => {
      if(!eventSearchWrap) return;
      if(eventSearchWrap.contains(event.target)) return;
      if(eventSearchWrap.classList.contains("is-range-open")){
        setRangeOpen(false);
      }
      if(eventSearchWrap.classList.contains("is-open") && !normalizeSearchQuery(eventSearchInput?.value)){
        setEventSearchOpen(false);
      }
    });
    document.addEventListener("keydown", (event) => {
      if(event.key !== "Escape") return;
      if(eventSearchWrap?.classList.contains("is-range-open")){
        setRangeOpen(false);
      }
    });

    document.querySelectorAll("[data-cal-view]").forEach(btn => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.calView;
        if(view === "year"){
          setActiveView(view);
          const year = calendar.getDate().getFullYear();
          refreshYearView(year);
          setYearViewVisible(true, "year");
          return;
        }
        calendar.changeView(view);
        setActiveView(view);
        setYearViewVisible(false, view);
      });
    });

    // quick add
    // NLP auto-grow
    setupShadowAutoGrow("nlp-unified-text");
    const composerWrap = document.querySelector(".composer-input-wrap");
    if(composerWrap){
      requestAnimationFrame(() => {
        const rect = composerWrap.getBoundingClientRect();
        if(rect.height){
          composerWrap.style.setProperty("--composer-pill-radius", `${Math.round(rect.height / 2)}px`);
        }
      });
    }
    setupImageComposer();
    initReasoningEffortControl();
    setupEventModalControls();

    const toggle = document.getElementById("nlp-mode-toggle");
    const actionBtn = document.getElementById("nlp-action-btn");

    setUnifiedMode(toggle.checked);
    toggle.addEventListener("change", () => setUnifiedMode(toggle.checked));
    actionBtn.addEventListener("click", runUnifiedNlpAction);

    setDefaultDateRange("delete-scope-start", "delete-scope-end", 30);
    updateUndoButton();
    const undoBtn = document.getElementById("undo-last-btn");
    if(undoBtn){
      undoBtn.addEventListener("click", undoLastBatch);
    }

    const recentBtn = document.getElementById("recent-added-btn");
    if(recentBtn){
      recentBtn.style.display = "";
      recentBtn.addEventListener("click", openRecentModal);
    }

    const toolBtn = document.getElementById("tool-menu-btn");
    const toolDrawer = document.getElementById("tool-drawer");
    const toolOverlay = document.getElementById("tool-overlay");
    const toolClose = document.getElementById("tool-close-btn");
    const openToolDrawer = () => {
      if(!toolDrawer || !toolOverlay) return;
      toolDrawer.classList.add("active");
      toolOverlay.classList.add("active");
      document.body.classList.add("drawer-open");
    };
    const closeToolDrawer = () => {
      if(!toolDrawer || !toolOverlay) return;
      toolDrawer.classList.remove("active");
      toolOverlay.classList.remove("active");
      document.body.classList.remove("drawer-open");
    };
    toolBtn?.addEventListener("click", openToolDrawer);
    toolOverlay?.addEventListener("click", closeToolDrawer);
    toolClose?.addEventListener("click", closeToolDrawer);
    document.addEventListener("keydown", (e) => {
      if(e.key === "Escape"){
        closeToolDrawer();
      }
    });
    window.addEventListener("resize", () => {
      requestAnimationFrame(updateViewSwitchIndicators);
    });

    const ta = document.getElementById("nlp-unified-text");
    if(ta){
      ta.addEventListener("compositionstart", () => { nlpInputComposing = true; });
      ta.addEventListener("compositionend", () => { nlpInputComposing = false; });
      ta.addEventListener("blur", () => { nlpInputComposing = false; });

      ta.addEventListener("keydown", (e) => {
        if(e.key === "Enter" && !e.shiftKey){
          if(e.isComposing || nlpInputComposing) return;
          e.preventDefault();
          runUnifiedNlpAction();
        }
      });
    }

    // pill placeholders
    function setupPillPlaceholder(inputId){
      const input = document.getElementById(inputId);
      const wrap = input?.closest(".pill-field");
      const ph = wrap?.querySelector(".pill-placeholder");
      if(!input || !ph) return;

      const update = () => {
        if(input.value && input.value.trim() !== ""){
          ph.classList.add("hidden");
          input.classList.add("has-value");
        }else{
          ph.classList.remove("hidden");
          input.classList.remove("has-value");
        }
      };
      input.addEventListener("input", update);
      input.addEventListener("change", update);
      update();
    }
    setupPillPlaceholder("start");
    setupPillPlaceholder("end");
    setupPillPlaceholder("location");

    // confirm modal bindings
    document.getElementById("confirm-close").addEventListener("click", closeConfirm);
    document.getElementById("confirm-cancel").addEventListener("click", closeConfirm);
    document.getElementById("confirm-overlay").addEventListener("click", (e) => {
      if(e.target && e.target.id === "confirm-overlay") closeConfirm();
    });

    document.getElementById("recent-close").addEventListener("click", closeRecentModal);
    document.getElementById("recent-cancel").addEventListener("click", closeRecentModal);
    document.getElementById("recent-overlay").addEventListener("click", (e) => {
      if(e.target && e.target.id === "recent-overlay") closeRecentModal();
    });

    document.getElementById("confirm-ok").addEventListener("click", async () => {
      if(confirmState.mode === "add"){
        const topChecks = Array.from(document.querySelectorAll("input[type=checkbox][data-role='add-top']"));
        const chosenIdx = topChecks
          .filter(x => x.checked)
          .map(x => parseInt(x.dataset.addIndex, 10))
          .filter(n => Number.isFinite(n));

        const occSelected = {};
        document.querySelectorAll("input[type=checkbox][data-role='add-occurrence']").forEach(el => {
          const addIdx = parseInt(el.dataset.addIndex || "", 10);
          const occIdx = parseInt(el.dataset.addOccurrenceIndex || "", 10);
          if(!Number.isFinite(addIdx) || !Number.isFinite(occIdx)) return;
          if(!occSelected[addIdx]) occSelected[addIdx] = [];
          if(el.checked){
            occSelected[addIdx].push(occIdx);
          }
        });

        const selectedEntries = [];
        chosenIdx.forEach(idx => {
          const base = confirmState.addItems[idx];
          if(!base) return;

          if((base.type || "").toLowerCase() === "recurring"){
            const occList = occSelected[idx];
            if(Array.isArray(occList) && occList.length > 0){
              selectedEntries.push({ idx, item: { ...base, selected_occurrence_indexes: occList } });
              return;
            }
            if(occSelected[idx] === undefined){
              selectedEntries.push({ idx, item: base });
              return;
            }
            return;
          }
          selectedEntries.push({ idx, item: base });
        });

        const selected = selectedEntries.map(entry => entry.item);

        if(selected.length === 0){
          showWarning("추가할 항목을 선택해주세요.");
          return;
        }

        for(const entry of selectedEntries){
          const original = confirmState.addItems[entry.idx];
          if(original && original.requires_end_confirmation){
            const selection = getRecurrenceEndSelection(entry.idx);
            if(!selection){
              showWarning("반복 종료 방식을 선택해주세요.");
              return;
            }
            if(selection.mode === "until" && !selection.value){
              showWarning("반복 종료 날짜를 입력해주세요.");
              return;
            }
            if(selection.mode === "count" && (!selection.value || selection.value <= 0)){
              showWarning("반복 횟수를 1 이상으로 입력해주세요.");
              return;
            }
            entry.item.recurring_end_override = selection;
          }
        }

        const res = await fetch(apiBase + "/nlp-apply-add", {
          method:"POST",
          headers:{ "Content-Type":"application/json" },
          body: JSON.stringify({ items: selected })
        });

        if(!res.ok){
          showWarning("일정 추가 적용에 실패했습니다.");
          return;
        }
        const created = await res.json();
        if(Array.isArray(created)){
          recordUndoBatch(created);
        }
        if(IS_GOOGLE_MODE){
          markGoogleCacheDirty();
        }else{
          markLocalCacheDirty();
        }

        closeConfirm();
        resetNlpComposerInputs();
        resetNlpConversation();
        await refreshAll();
        return;
      }

      if(confirmState.mode === "delete"){
        const ids = Array.from(document.querySelectorAll("input[type=checkbox][data-delete-id]"))
          .filter(x => x.checked)
          .map(x => parseInt(x.dataset.deleteId, 10))
          .filter(n => Number.isFinite(n));

        if(ids.length === 0){
          showWarning("삭제할 항목을 선택해주세요.");
          return;
        }

        const res = await fetch(apiBase + "/delete-by-ids", {
          method:"POST",
          headers:{ "Content-Type":"application/json" },
          body: JSON.stringify({ ids })
        });

        if(!res.ok){
          showWarning("일정 삭제 적용에 실패했습니다.");
          return;
        }
        markLocalCacheDirty();

        closeConfirm();
        resetNlpComposerInputs();
        await refreshAll();
        return;
      }
    });
  };

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", onDomReady);
  }else{
    onDomReady();
  }
