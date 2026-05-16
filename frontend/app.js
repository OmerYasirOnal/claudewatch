function appRoot() {
  return {
    view: "dashboard",
    sessions: [],
    history: [],
    stats: {},
    health: { iterm_api: null, tmux_available: null, log_dir_found: null, issues: [] },
    config: { pricing: {}, notifications: {}, remote_control: { enabled: false } },
    filter: "All",
    filters: ["All", "iTerm", "Tmux", "Headless", "Working", "Idle", "High-cost", "Bookmarked"],
    detailPid: null,
    detail: null,
    showNewModal: false,
    newSession: { cwd: "", window_type: "new-window", skipPerm: true, customFlags: "" },
    newSessionError: "",
    _sse: null,
    _sseReconnectDelay: 1000,
    errorBanner: null,  // { text, ts } | null
    cardVisibility: {
      active: true,
      sessions_today: true,
      active_tokens: true,
      active_cost: true,
      cost_today: true,
    },

    // F2 - Insights
    insightsData: { projects: [], hourly: { bins: [] } },
    _insightsTimer: null,

    // F3 - Search
    searchQuery: "",
    _searchDebounce: null,
    _searchQueryDebounced: "",

    // F4 - Keyboard shortcuts
    selectedPid: null,
    showShortcuts: false,
    _gPressed: false,
    _gPressedAt: 0,

    // Chat panel (remote-control read+send)
    chatPanelPid: null,
    chatEntries: [],
    chatInput: "",
    chatSending: false,
    chatRemoteEnabled: false,
    chatError: null,
    chatConfirmOpen: false,
    _chatSSE: null,

    // F1 - subagents
    expandedSubagents: {},

    // F5 - bookmarks
    bookmarks: [],

    // F6 - notes
    notes: {},
    notesEditPid: null,
    notesEditKey: null,
    notesEditText: "",

    // F7 - appearance
    appearance: "dark",

    // F8 - density
    density: "comfortable",

    async init() {
      this._loadLocalPrefs();
      this._applyAppearance();
      this._applyDensity();
      await Promise.all([this.loadHealth(), this.loadSessions(), this.loadStats(), this.loadConfig()]);
      this.connectSSE();
      this._startNowTimer();
      this._installKeydown();
      setInterval(() => this.loadStats(), 5000);
      setInterval(() => this.loadHealth(), 30000);
      // Watch insights view
      this.$watch && this.$watch('view', (v) => this._onViewChange(v));
    },

    _loadLocalPrefs() {
      try {
        const raw = localStorage.getItem("claudewatch.cardVisibility");
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === "object") {
            this.cardVisibility = { ...this.cardVisibility, ...parsed };
          }
        }
      } catch (e) { /* ignore */ }
      try {
        const bm = localStorage.getItem("claudewatch.bookmarks");
        if (bm) {
          const parsed = JSON.parse(bm);
          if (Array.isArray(parsed)) this.bookmarks = parsed;
        }
      } catch (e) { /* ignore */ }
      try {
        const nt = localStorage.getItem("claudewatch.notes");
        if (nt) {
          const parsed = JSON.parse(nt);
          if (parsed && typeof parsed === "object") this.notes = parsed;
        }
      } catch (e) { /* ignore */ }
      try {
        const app = localStorage.getItem("claudewatch.appearance");
        if (app === "light" || app === "dark") this.appearance = app;
      } catch (e) { /* ignore */ }
      try {
        const den = localStorage.getItem("claudewatch.density");
        if (den === "comfortable" || den === "compact") this.density = den;
      } catch (e) { /* ignore */ }
      try {
        const ex = localStorage.getItem("claudewatch.expandedSubagents");
        if (ex) {
          const parsed = JSON.parse(ex);
          if (parsed && typeof parsed === "object") this.expandedSubagents = parsed;
        }
      } catch (e) { /* ignore */ }
    },

    saveCardVisibility() {
      try {
        localStorage.setItem("claudewatch.cardVisibility", JSON.stringify(this.cardVisibility));
      } catch (e) { console.warn("save cardVisibility failed", e); }
    },
    saveBookmarks() {
      try { localStorage.setItem("claudewatch.bookmarks", JSON.stringify(this.bookmarks)); }
      catch (e) { console.warn("save bookmarks failed", e); }
    },
    saveNotes() {
      try { localStorage.setItem("claudewatch.notes", JSON.stringify(this.notes)); }
      catch (e) { console.warn("save notes failed", e); }
    },
    saveExpandedSubagents() {
      try { localStorage.setItem("claudewatch.expandedSubagents", JSON.stringify(this.expandedSubagents)); }
      catch (e) { /* ignore */ }
    },
    saveAppearance() {
      try { localStorage.setItem("claudewatch.appearance", this.appearance); }
      catch (e) { /* ignore */ }
      this._applyAppearance();
    },
    saveDensity() {
      try { localStorage.setItem("claudewatch.density", this.density); }
      catch (e) { /* ignore */ }
      this._applyDensity();
    },
    _applyAppearance() {
      const html = document.documentElement;
      const body = document.body;
      if (this.appearance === "light") {
        html.classList.add("light"); html.classList.remove("dark");
        if (body) { body.classList.add("light"); body.classList.remove("dark"); }
      } else {
        html.classList.add("dark"); html.classList.remove("light");
        if (body) { body.classList.add("dark"); body.classList.remove("light"); }
      }
    },
    _applyDensity() {
      const html = document.documentElement;
      if (this.density === "compact") {
        html.classList.add("compact"); html.classList.remove("comfortable");
      } else {
        html.classList.add("comfortable"); html.classList.remove("compact");
      }
    },

    _setError(msg) {
      this.errorBanner = { text: msg, ts: Date.now() };
      setTimeout(() => {
        if (this.errorBanner && Date.now() - this.errorBanner.ts >= 8000) {
          this.errorBanner = null;
        }
      }, 8500);
    },

    async loadHealth() {
      let r;
      try {
        r = await fetch("/api/health");
        if (r.ok) { this.health = await r.json(); return; }
      } catch (e) { console.warn("health failed", e); }
      this._setError(`Failed to load /api/health: HTTP ${r?.status ?? '???'}`);
    },
    async loadSessions() {
      let r;
      try {
        r = await fetch("/api/sessions");
        if (r.ok) { this.sessions = await r.json(); return; }
      } catch (e) { console.warn("sessions failed", e); }
      this._setError(`Failed to load /api/sessions: HTTP ${r?.status ?? '???'}`);
    },
    async loadStats() {
      let r;
      try {
        r = await fetch("/api/stats");
        if (r.ok) { this.stats = await r.json(); return; }
      } catch (e) { /* ignore */ }
      this._setError(`Failed to load /api/stats: HTTP ${r?.status ?? '???'}`);
    },
    async loadHistory() {
      let r;
      try {
        r = await fetch("/api/history");
        if (r.ok) { this.history = await r.json(); return; }
      } catch (e) { /* ignore */ }
      this._setError(`Failed to load /api/history: HTTP ${r?.status ?? '???'}`);
    },
    async loadConfig() {
      let r;
      try {
        r = await fetch("/api/config");
        if (r.ok) {
          this.config = await r.json();
          if (!this.config.notifications) this.config.notifications = {};
          if (!this.config.remote_control) this.config.remote_control = { enabled: false };
          this.chatRemoteEnabled = !!(this.config.remote_control && this.config.remote_control.enabled);
          return;
        }
      } catch (e) { /* ignore */ }
      this._setError(`Failed to load /api/config: HTTP ${r?.status ?? '???'}`);
    },
    async saveConfig(updates) {
      try {
        const r = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updates),
        });
        if (r.ok) {
          this.config = await r.json();
          if (!this.config.notifications) this.config.notifications = {};
          if (!this.config.remote_control) this.config.remote_control = { enabled: false };
          this.chatRemoteEnabled = !!(this.config.remote_control && this.config.remote_control.enabled);
        }
      } catch (e) { console.warn("save config failed", e); }
    },
    async saveRemoteControl() {
      const enabled = !!(this.config.remote_control && this.config.remote_control.enabled);
      await this.saveConfig({ remote_control: { enabled } });
    },
    async updatePricing(model, key, value) {
      const v = parseFloat(value);
      if (Number.isNaN(v)) return;
      const next = { ...(this.config.pricing || {}) };
      next[model] = { ...(next[model] || {}), [key]: v };
      this.config.pricing = next;
      await this.saveConfig({ pricing: next });
    },
    async saveNotificationConfig() {
      const n = this.config.notifications || {};
      await this.saveConfig({ notifications: {
        enabled: !!n.enabled,
        on_session_end: !!n.on_session_end,
        on_high_cost: !!n.on_high_cost,
        cost_threshold_usd: Number(n.cost_threshold_usd) || 0,
      }});
    },
    async loadDetail(pid) {
      let r;
      try {
        r = await fetch(`/api/sessions/${pid}`);
        if (r.ok) { this.detail = await r.json(); return; }
      } catch (e) { /* ignore */ }
      this._setError(`Failed to load /api/sessions/${pid}: HTTP ${r?.status ?? '???'}`);
    },

    // F2 - Insights data
    async loadInsights() {
      try {
        const [pRes, hRes] = await Promise.all([
          fetch("/api/projects").catch(() => null),
          fetch("/api/history/hourly?hours=24").catch(() => null),
        ]);
        const projects = pRes && pRes.ok ? await pRes.json() : [];
        const hourly = hRes && hRes.ok ? await hRes.json() : { bins: [] };
        this.insightsData = {
          projects: Array.isArray(projects) ? projects : [],
          hourly: hourly && hourly.bins ? hourly : { bins: [] },
        };
        // Defer rendering until DOM updated
        this.$nextTick && this.$nextTick(() => this._renderInsightsCharts());
        setTimeout(() => this._renderInsightsCharts(), 50);
      } catch (e) {
        console.warn("insights load failed", e);
      }
    },
    _onViewChange(v) {
      if (v === "insights") {
        this.loadInsights();
        if (this._insightsTimer) clearInterval(this._insightsTimer);
        this._insightsTimer = setInterval(() => this.loadInsights(), 30000);
      } else {
        if (this._insightsTimer) { clearInterval(this._insightsTimer); this._insightsTimer = null; }
      }
      if (v === "history") this.loadHistory();
    },

    _renderInsightsCharts() {
      const barCanvas = document.getElementById("insights-bar");
      if (barCanvas) {
        const bins = (this.insightsData.hourly && this.insightsData.hourly.bins) || [];
        const data = bins.map((b) => ({
          label: (b.hour || "").slice(11, 16) || (b.hour || ""),
          value: Number(b.cost) || 0,
        }));
        this.drawBarChart(barCanvas, data);
      }
      const donutCanvas = document.getElementById("insights-donut");
      if (donutCanvas) {
        const buckets = {};
        for (const s of this.sessions) {
          const m = s.model || "unknown";
          const t = (s.usage && (s.usage.input_tokens || 0) + (s.usage.output_tokens || 0)) || 0;
          buckets[m] = (buckets[m] || 0) + t;
        }
        const palette = ["#ec4899","#10b981","#f59e0b","#3b82f6","#a855f7","#ef4444","#06b6d4","#84cc16"];
        const data = Object.entries(buckets)
          .filter(([_, v]) => v > 0)
          .map(([k, v], i) => ({ label: k, value: v, color: palette[i % palette.length] }));
        this.drawDonut(donutCanvas, data);
      }
    },

    drawBarChart(canvas, data) {
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const W = Math.max(200, Math.floor(rect.width));
      const H = Math.max(120, Math.floor(rect.height || 180));
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + "px"; canvas.style.height = H + "px";
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H);
      const light = this.appearance === "light";
      const axisColor = light ? "#52525b" : "#a1a1aa";
      const gridColor = light ? "#e4e4e7" : "#27272a";
      const barColor = "#10b981";
      const padL = 36, padR = 8, padT = 10, padB = 22;
      const innerW = W - padL - padR, innerH = H - padT - padB;
      const max = Math.max(0.01, ...data.map((d) => d.value));
      // grid lines
      ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
      ctx.fillStyle = axisColor; ctx.font = "10px ui-sans-serif, -apple-system";
      for (let i = 0; i <= 4; i++) {
        const y = padT + (innerH * i) / 4;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        const v = max * (1 - i / 4);
        ctx.fillText("$" + v.toFixed(2), 2, y + 3);
      }
      if (!data.length) {
        ctx.fillStyle = axisColor;
        ctx.fillText("No data", padL + innerW / 2 - 20, padT + innerH / 2);
        return;
      }
      const n = data.length;
      const gap = 2;
      const barW = Math.max(2, (innerW - gap * (n - 1)) / n);
      data.forEach((d, i) => {
        const h = max > 0 ? (d.value / max) * innerH : 0;
        const x = padL + i * (barW + gap);
        const y = padT + innerH - h;
        ctx.fillStyle = barColor;
        ctx.fillRect(x, y, barW, h);
      });
      // x-axis labels (every Nth)
      ctx.fillStyle = axisColor;
      const stride = Math.max(1, Math.ceil(n / 6));
      data.forEach((d, i) => {
        if (i % stride !== 0) return;
        const x = padL + i * (barW + gap) + barW / 2;
        const label = d.label || "";
        const w = ctx.measureText(label).width;
        ctx.fillText(label, x - w / 2, H - 6);
      });
    },

    drawDonut(canvas, data) {
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const W = Math.max(200, Math.floor(rect.width));
      const H = Math.max(160, Math.floor(rect.height || 200));
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + "px"; canvas.style.height = H + "px";
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H);
      const light = this.appearance === "light";
      const labelColor = light ? "#27272a" : "#e4e4e7";
      const total = data.reduce((a, b) => a + (Number(b.value) || 0), 0);
      const cx = H / 2 + 4, cy = H / 2, r = Math.min(H, W) / 2 - 12, inner = r * 0.6;
      if (total <= 0) {
        ctx.fillStyle = light ? "#71717a" : "#a1a1aa";
        ctx.font = "12px ui-sans-serif, -apple-system";
        ctx.fillText("No active token usage", 10, H / 2);
        return;
      }
      let angle = -Math.PI / 2;
      for (const d of data) {
        const v = Number(d.value) || 0;
        const slice = (v / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, angle, angle + slice);
        ctx.closePath();
        ctx.fillStyle = d.color || "#10b981";
        ctx.fill();
        angle += slice;
      }
      // inner hole
      ctx.beginPath();
      ctx.fillStyle = light ? "#fafafa" : "#09090b";
      ctx.arc(cx, cy, inner, 0, Math.PI * 2);
      ctx.fill();
      // legend
      ctx.font = "11px ui-sans-serif, -apple-system";
      ctx.fillStyle = labelColor;
      let ly = 18;
      const lx = H + 16;
      for (const d of data) {
        ctx.fillStyle = d.color || "#10b981";
        ctx.fillRect(lx, ly - 9, 10, 10);
        ctx.fillStyle = labelColor;
        const pct = ((d.value / total) * 100).toFixed(1);
        const label = `${d.label} · ${pct}%`;
        ctx.fillText(label, lx + 16, ly);
        ly += 16;
        if (ly > H - 4) break;
      }
    },

    connectSSE() {
      try {
        this._sse = new EventSource("/api/stream");
        this._sse.onopen = () => {
          this._sseReconnectDelay = 1000;
        };
        this._sse.addEventListener("snapshot", (e) => {
          const data = JSON.parse(e.data);
          if (data.sessions) this.sessions = data.sessions;
        });
        this._sse.addEventListener("session.started", (e) => {
          const d = JSON.parse(e.data);
          if (d.session) this._upsertSession(d.session);
        });
        this._sse.addEventListener("session.updated", (e) => {
          const d = JSON.parse(e.data);
          if (d.session) this._upsertSession(d.session);
        });
        this._sse.addEventListener("session.ended", (e) => {
          const d = JSON.parse(e.data);
          if (d.pid) this.sessions = this.sessions.filter((s) => s.pid !== d.pid);
        });
        this._sse.onerror = () => {
          if (this._sse) this._sse.close();
          this._setError("Lost connection to daemon — retrying...");
          setTimeout(() => this.connectSSE(), this._sseReconnectDelay);
          this._sseReconnectDelay = Math.min(this._sseReconnectDelay * 2, 30000);
        };
      } catch (e) { console.warn("SSE failed", e); }
    },
    _upsertSession(sess) {
      const idx = this.sessions.findIndex((s) => s.pid === sess.pid);
      if (idx >= 0) this.sessions.splice(idx, 1, sess);
      else this.sessions.push(sess);
    },

    // F3 - search debounce
    onSearchInput(value) {
      this.searchQuery = value;
      if (this._searchDebounce) clearTimeout(this._searchDebounce);
      this._searchDebounce = setTimeout(() => {
        this._searchQueryDebounced = (this.searchQuery || "").trim().toLowerCase();
      }, 150);
    },

    _sessionMatchesSearch(s) {
      const q = this._searchQueryDebounced;
      if (!q) return true;
      const haystackParts = [
        s.cwd,
        s.model,
        s.current_task_subject,
        s.current_task_active_form,
        s.iterm_tab_title,
        s.tool_calls && s.tool_calls.last_used,
      ];
      const hay = haystackParts.filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    },

    visibleSessions() {
      let arr = [...this.sessions];
      switch (this.filter) {
        case "iTerm": arr = arr.filter((s) => s.location_type === "iterm"); break;
        case "Tmux": arr = arr.filter((s) => s.location_type === "tmux"); break;
        case "Headless": arr = arr.filter((s) => s.location_type === "headless"); break;
        case "Working": arr = arr.filter((s) => s.status === "working" || s.is_in_flight); break;
        case "Idle": arr = arr.filter((s) => s.status === "idle" && !s.is_in_flight); break;
        case "High-cost":
          arr = arr.filter((s) => s.usage && (s.usage.cost_estimate_usd || 0) >= 1.0);
          break;
        case "Bookmarked":
          arr = arr.filter((s) => this.isBookmarked(s));
          break;
      }
      // search
      arr = arr.filter((s) => this._sessionMatchesSearch(s));
      // Bookmarks first, then most recently-active
      arr.sort((a, b) => {
        const ab = this.isBookmarked(a) ? 1 : 0;
        const bb = this.isBookmarked(b) ? 1 : 0;
        if (ab !== bb) return bb - ab;
        return (b.last_activity_at || "").localeCompare(a.last_activity_at || "");
      });
      return arr;
    },

    nowTick: 0,
    _startNowTimer() {
      if (this._tickTimer) return;
      this._tickTimer = setInterval(() => { this.nowTick = Date.now(); }, 1000);
    },

    fmtElapsed(seconds, fromIso) {
      void this.nowTick;
      let s = Number(seconds || 0);
      if (fromIso) {
        const driftMs = Date.now() - new Date(fromIso).getTime();
        if (Number.isFinite(driftMs) && driftMs > 0) s = Math.floor(driftMs / 1000);
      }
      if (s < 0) s = 0;
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      if (h > 0) return `${h}h ${m}m ${sec}s`;
      if (m > 0) return `${m}m ${sec}s`;
      return `${sec}s`;
    },

    relTime(iso) {
      void this.nowTick;
      if (!iso) return "—";
      const delta = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
      if (delta < 5) return "just now";
      if (delta < 60) return `${delta}s ago`;
      if (delta < 3600) return `${Math.floor(delta / 60)}m ${delta % 60}s ago`;
      const h = Math.floor(delta / 3600);
      return `${h}h ${Math.floor((delta % 3600) / 60)}m ago`;
    },

    fmtNum(n) {
      if (n === null || n === undefined) return "—";
      const num = Number(n);
      if (!Number.isFinite(num)) return "—";
      if (num < 0) return String(num);
      if (num >= 1e15) return (num / 1e15).toFixed(2) + "Q";
      if (num >= 1e12) return (num / 1e12).toFixed(2) + "T";
      if (num >= 1e9)  return (num / 1e9).toFixed(2) + "B";
      if (num >= 1e6)  return (num / 1e6).toFixed(2) + "M";
      if (num >= 1e3)  return (num / 1e3).toFixed(1) + "K";
      return String(num);
    },
    fmtMoney(n) {
      if (n === null || n === undefined) return "—";
      if (!Number.isFinite(Number(n))) return "—";
      return "$" + Number(n).toFixed(2);
    },
    fmtDuration(s) {
      if (!s) return "0s";
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
      if (h) return `${h}h ${m}m`;
      if (m) return `${m}m`;
      return `${s}s`;
    },
    fmtTime(iso) {
      if (!iso) return "—";
      return new Date(iso).toLocaleString();
    },

    // F1 - subagents
    sessionKey(s) {
      return `${s.pid}:${s.started_at || ""}`;
    },
    subagentSummary(s) {
      const arr = s.subagents || [];
      if (!arr.length) return null;
      const completed = arr.filter((x) => x.status === "completed").length;
      const pending = arr.filter((x) => x.status === "pending").length;
      const other = arr.length - completed - pending;
      const parts = [];
      if (completed) parts.push(`${completed} completed`);
      if (pending) parts.push(`${pending} running`);
      if (other) parts.push(`${other} other`);
      return { total: arr.length, text: parts.join(", ") };
    },
    toggleSubagents(s) {
      const k = this.sessionKey(s);
      this.expandedSubagents[k] = !this.expandedSubagents[k];
      this.saveExpandedSubagents();
    },
    isSubagentsExpanded(s) {
      return !!this.expandedSubagents[this.sessionKey(s)];
    },
    subagentDuration(sa) {
      void this.nowTick;
      if (sa.duration_seconds != null) return this.fmtElapsed(sa.duration_seconds, null);
      if (sa.started_at && !sa.ended_at) {
        return this.fmtElapsed(0, sa.started_at);
      }
      return "—";
    },

    // F5 - bookmarks
    isBookmarked(s) {
      return this.bookmarks.includes(this.sessionKey(s));
    },
    toggleBookmark(s) {
      const k = this.sessionKey(s);
      const i = this.bookmarks.indexOf(k);
      if (i >= 0) this.bookmarks.splice(i, 1);
      else this.bookmarks.push(k);
      this.saveBookmarks();
    },

    // F6 - notes
    sessionNote(s) {
      return this.notes[this.sessionKey(s)] || "";
    },
    openNoteEditor(s) {
      this.notesEditKey = this.sessionKey(s);
      this.notesEditPid = s.pid;
      this.notesEditText = this.notes[this.notesEditKey] || "";
    },
    saveNoteEditor() {
      if (!this.notesEditKey) return;
      const text = (this.notesEditText || "").trim();
      if (text) this.notes[this.notesEditKey] = text;
      else delete this.notes[this.notesEditKey];
      this.saveNotes();
      this.closeNoteEditor();
    },
    closeNoteEditor() {
      this.notesEditPid = null;
      this.notesEditKey = null;
      this.notesEditText = "";
    },

    // F10 - export
    exportSession(s) {
      const url = `/api/sessions/${s.pid}/export`;
      window.open(url, "_blank");
    },
    exportAll() {
      window.open("/api/export.csv?days=7", "_blank");
    },

    async focusSession(sess) {
      try {
        const r = await fetch(`/api/sessions/${sess.pid}/focus`, { method: "POST" });
        if (!r.ok) alert("Focus failed: " + r.status);
      } catch (e) { alert("Focus error: " + e); }
    },
    async haltSession(sess) {
      if (!confirm(`Halt PID ${sess.pid} in ${sess.cwd}?\nUnsaved work may be lost.`)) return;
      try {
        const r = await fetch(`/api/sessions/${sess.pid}/halt`, { method: "POST" });
        if (!r.ok) alert("Halt failed: " + r.status);
      } catch (e) { alert("Halt error: " + e); }
    },
    openNewFromCwd(cwd) {
      this.newSession.cwd = cwd || "";
      this.showNewModal = true;
      this.newSessionError = "";
    },
    async submitNew() {
      this.newSessionError = "";
      const flags = [];
      if (this.newSession.skipPerm) flags.push("--dangerously-skip-permissions");
      if (this.newSession.customFlags) {
        for (const tok of this.newSession.customFlags.split(/\s+/).filter(Boolean)) {
          flags.push(tok);
        }
      }
      try {
        const r = await fetch("/api/sessions/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            cwd: this.newSession.cwd,
            window_type: this.newSession.window_type,
            flags,
            command: "claude",
          }),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          this.newSessionError = err.detail || `HTTP ${r.status}`;
          return;
        }
        this.showNewModal = false;
      } catch (e) {
        this.newSessionError = String(e);
      }
    },

    // F2 - jump to dashboard filtered by cwd via search bar
    jumpToProject(cwd) {
      this.view = "dashboard";
      this.searchQuery = cwd;
      this._searchQueryDebounced = (cwd || "").toLowerCase();
    },

    // F4 - keyboard shortcuts
    _installKeydown() {
      window.addEventListener("keydown", (e) => this._onKeydown(e));
    },
    _onKeydown(e) {
      const target = e.target;
      const isEditable = target && (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      );
      // Esc closes modals (works even inside inputs to escape)
      if (e.key === "Escape") {
        if (this.chatConfirmOpen) { this.chatConfirmOpen = false; e.preventDefault(); return; }
        if (this.showShortcuts) { this.showShortcuts = false; e.preventDefault(); return; }
        if (this.notesEditPid !== null) { this.closeNoteEditor(); e.preventDefault(); return; }
        if (this.chatPanelPid !== null) { this.closeChat(); e.preventDefault(); return; }
        if (this.detailPid !== null) { this.detailPid = null; this.detail = null; e.preventDefault(); return; }
        if (this.showNewModal) { this.showNewModal = false; e.preventDefault(); return; }
        if (isEditable && target.blur) target.blur();
        return;
      }
      if (isEditable) return;
      // "/" focuses search
      if (e.key === "/") {
        const el = document.getElementById("global-search");
        if (el) { el.focus(); e.preventDefault(); }
        return;
      }
      if (e.key === "?") {
        this.showShortcuts = true;
        e.preventDefault();
        return;
      }
      // g-prefix tab switches
      if (e.key === "g") {
        this._gPressed = true;
        this._gPressedAt = Date.now();
        setTimeout(() => {
          if (Date.now() - this._gPressedAt >= 1200) this._gPressed = false;
        }, 1300);
        return;
      }
      if (this._gPressed && Date.now() - this._gPressedAt < 1200) {
        if (e.key === "i") { this.view = "insights"; this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "d") { this.view = "dashboard"; this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "h") { this.view = "history"; this.loadHistory(); this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "s") { this.view = "settings"; this._gPressed = false; e.preventDefault(); return; }
        this._gPressed = false;
      }
      // Dashboard-specific
      if (this.view !== "dashboard") return;
      const list = this.visibleSessions();
      if (!list.length) return;
      if (e.key === "j" || e.key === "k") {
        let idx = list.findIndex((s) => s.pid === this.selectedPid);
        if (idx < 0) idx = 0;
        else idx = e.key === "j" ? Math.min(list.length - 1, idx + 1) : Math.max(0, idx - 1);
        this.selectedPid = list[idx].pid;
        this._scrollSelectedIntoView();
        e.preventDefault();
        return;
      }
      const sel = list.find((s) => s.pid === this.selectedPid);
      if (!sel) return;
      if (e.key === "Enter") {
        this.detailPid = sel.pid;
        this.loadDetail(sel.pid);
        e.preventDefault();
      } else if (e.key === "f") {
        this.focusSession(sel);
        e.preventDefault();
      } else if (e.key === "h") {
        this.haltSession(sel);
        e.preventDefault();
      }
    },
    _scrollSelectedIntoView() {
      this.$nextTick && this.$nextTick(() => {
        if (this.selectedPid == null) return;
        const el = document.querySelector(`[data-session-pid="${this.selectedPid}"]`);
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    },

    // Chat panel (Part 2)
    openChat(pid) {
      if (pid == null) return;
      this.closeChat();
      this.chatPanelPid = pid;
      this.chatEntries = [];
      this.chatInput = "";
      this.chatError = null;
      try {
        this._chatSSE = new EventSource(`/api/sessions/${pid}/log-stream`);
        this._chatSSE.addEventListener("snapshot", (e) => {
          try {
            const data = JSON.parse(e.data);
            this.chatEntries = Array.isArray(data.entries) ? data.entries : [];
            this._scrollChatToBottom();
          } catch (err) { console.warn("chat snapshot parse failed", err); }
        });
        this._chatSSE.addEventListener("append", (e) => {
          try {
            const data = JSON.parse(e.data);
            const more = Array.isArray(data.entries) ? data.entries : [];
            if (more.length) {
              this.chatEntries.push(...more);
              this._scrollChatToBottom();
            }
          } catch (err) { console.warn("chat append parse failed", err); }
        });
        this._chatSSE.onerror = () => {
          // Best-effort: surface a small inline hint but don't spam top banner
          this.chatError = "Lost connection to log stream";
        };
      } catch (e) {
        this.chatError = "Failed to open log stream: " + e;
      }
    },
    closeChat() {
      if (this._chatSSE) {
        try { this._chatSSE.close(); } catch (e) { /* ignore */ }
        this._chatSSE = null;
      }
      this.chatPanelPid = null;
      this.chatEntries = [];
      this.chatInput = "";
      this.chatSending = false;
      this.chatError = null;
      this.chatConfirmOpen = false;
    },
    _scrollChatToBottom() {
      this.$nextTick && this.$nextTick(() => {
        const el = document.getElementById("cw-chat-scroll");
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    _hasConfirmedRemoteSend() {
      try { return localStorage.getItem("claudewatch.confirmedRemoteSend") === "1"; }
      catch (e) { return false; }
    },
    _markConfirmedRemoteSend() {
      try { localStorage.setItem("claudewatch.confirmedRemoteSend", "1"); }
      catch (e) { /* ignore */ }
    },
    requestSendChat() {
      if (this.chatPanelPid == null) return;
      if (!this.chatRemoteEnabled) {
        this.chatError = "Enable remote control in Settings to send messages";
        return;
      }
      const text = (this.chatInput || "").trim();
      if (!text) return;
      if (!this._hasConfirmedRemoteSend()) {
        this.chatConfirmOpen = true;
        return;
      }
      this.sendChat();
    },
    confirmAndSend() {
      this._markConfirmedRemoteSend();
      this.chatConfirmOpen = false;
      this.sendChat();
    },
    async sendChat() {
      if (this.chatPanelPid == null) return;
      const pid = this.chatPanelPid;
      const text = (this.chatInput || "").trim();
      if (!text) return;
      this.chatSending = true;
      this.chatError = null;
      let r;
      try {
        r = await fetch(`/api/sessions/${pid}/send-text`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, submit: true }),
        });
      } catch (e) {
        this.chatError = "Network error: " + e;
        this.chatSending = false;
        return;
      }
      if (r.ok) {
        this.chatInput = "";
      } else if (r.status === 403) {
        this.chatError = "Enable remote control in Settings to send messages";
        this.chatRemoteEnabled = false;
      } else if (r.status === 413) {
        this.chatError = "Message too long (max 4096 chars)";
      } else {
        let detail = "";
        try { const j = await r.json(); detail = j.detail || ""; } catch (e) { /* ignore */ }
        this.chatError = `Send failed: HTTP ${r.status}${detail ? " · " + detail : ""}`;
      }
      this.chatSending = false;
    },

    // Rendering helpers for chat entries
    chatEntryRole(entry) {
      const t = entry && entry.type;
      if (t === "user") return "user";
      if (t === "assistant") return "assistant";
      if (t === "tool_result" || t === "user_tool_result") return "tool_result";
      return "system";
    },
    chatEntryText(entry) {
      if (!entry) return "";
      // Privacy mode: redacted entries come through with no text/content.
      const msg = entry.message || {};
      // Pure string content
      if (typeof msg.content === "string") return msg.content;
      // Top-level text fields some entries use
      if (typeof entry.text === "string" && entry.text) return entry.text;
      // Array content — concatenate text blocks
      if (Array.isArray(msg.content)) {
        const texts = [];
        for (const c of msg.content) {
          if (!c) continue;
          if (typeof c === "string") { texts.push(c); continue; }
          if (c.type === "text" && typeof c.text === "string") texts.push(c.text);
          else if (c.type === "tool_result" && typeof c.content === "string") texts.push(c.content);
        }
        if (texts.length) return texts.join("\n");
      }
      return "";
    },
    chatEntryToolUses(entry) {
      const msg = entry && entry.message;
      if (!msg || !Array.isArray(msg.content)) return [];
      const out = [];
      for (const c of msg.content) {
        if (c && c.type === "tool_use") {
          let summary = "";
          try {
            const inp = c.input || {};
            const keys = Object.keys(inp).slice(0, 3);
            const parts = keys.map((k) => {
              const v = inp[k];
              const sv = typeof v === "string" ? v : JSON.stringify(v);
              const short = sv && sv.length > 60 ? sv.slice(0, 60) + "…" : sv;
              return `${k}=${short}`;
            });
            summary = parts.join(" ");
          } catch (e) { /* ignore */ }
          out.push({ name: c.name || "tool", summary });
        }
      }
      return out;
    },
    chatEntryIsRedacted(entry) {
      if (!entry) return false;
      if (entry.redacted === true) return true;
      const msg = entry.message;
      // If we have an assistant/user entry but no message contents at all
      if (!msg) return false;
      const hasText = !!this.chatEntryText(entry);
      const hasTools = this.chatEntryToolUses(entry).length > 0;
      const hasAny = hasText || hasTools;
      // Heuristic: an assistant/user entry that explicitly has no content blocks
      if ((entry.type === "user" || entry.type === "assistant") && !hasAny) {
        const c = msg.content;
        if (c === undefined || c === null) return true;
        if (Array.isArray(c) && c.length === 0) return true;
        if (typeof c === "string" && c === "") return true;
      }
      return false;
    },
    chatEntryKey(entry, i) {
      if (!entry) return `e-${i}`;
      return entry.uuid || entry.id || entry.timestamp || `e-${i}`;
    },
  };
}
