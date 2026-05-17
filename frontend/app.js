function appRoot() {
  return {
    view: "dashboard",
    sessions: [],
    history: [],
    stats: {},
    health: { iterm_api: null, tmux_available: null, log_dir_found: null, issues: [] },
    config: { pricing: {}, notifications: {}, remote_control: { enabled: false }, plan: "api", editor: { enabled: false, command: "code" } },
    configDraft: {},
    configDirty: false,
    configSavingState: "idle",  // "idle" | "saving" | "saved" | "error"
    configSaveError: "",
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
      cost: true,
    },

    // F2 - Insights
    insightsData: { projects: [], hourly: { bins: [] } },
    _insightsTimer: null,

    // Cost forecast (Insights tab)
    forecastData: null,

    // Hourly cost trend (Insights tab — bar chart, last 7d)
    hourlyCostData: null,
    hourlyCostError: null,

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

    // F7 - appearance / theme
    // `theme` is the source of truth for the dark-mode toggle: one of
    // "light" | "dark" | "auto". "auto" defers to the OS-level
    // prefers-color-scheme via _themeMql below. Persisted in
    // localStorage at key "claudewatch.theme".
    //
    // `appearance` is retained for backwards-compatibility with the
    // previous 2-state implementation: any code or test that reads
    // `appearance` will see "light" | "dark" mirroring the resolved
    // theme (auto resolves to whatever the OS currently reports).
    theme: "auto",
    appearance: "dark",
    _themeMql: null,

    // F8 - density
    density: "comfortable",

    // Files tab
    fileChanges: [],
    fileChangesError: null,
    fileChangesUnavailable: false,
    filesMinutes: 10,
    filesKindFilter: "All",
    filesSearch: "",
    filesLastRefresh: null,
    filesSelected: null,         // { cwd, path } currently shown in side panel
    filesDiff: null,             // diff payload
    filesDiffError: null,
    filesDiffLoading: false,
    filesPidFilter: null,        // optional PID filter (deeplink from card)
    _filesTimer: null,
    _editorOpenStatus: null,     // transient string for "Open in editor" outcome

    // Tile mode
    tileMode: false,
    tilePreviews: {},            // pid -> { entries, error }
    _tileTimer: null,

    // Status tab (consumes /api/admin/*)
    adminStatus: null,
    adminLogs: { lines: [], path: "", size_bytes: 0, truncated: false },
    adminLogsGrep: "",
    adminLogsLineCount: 200,
    adminLogsTail: false,
    adminLogsLoading: false,
    adminStatusError: null,
    adminLogsError: null,
    // /api/metrics snapshot — populated by loadMetrics() on the Status tab.
    metricsData: null,
    metricsError: null,
    restartState: "idle",        // "idle" | "restarting"
    restartConfirmOpen: false,
    pruneHoursInput: 48,
    _adminPollTimer: null,
    _adminLogsGrepDebounce: null,
    _adminPostToast: null,        // transient "Pruned N rows" message

    // History tab enrichments
    hourlyHistory: { bins: [] },
    historyFilter: "all",         // "all" | "today" | "week" | "high-cost"
    historyExpandedKeys: {},      // key -> bool (object so Alpine reactivity works)

    async init() {
      this._loadLocalPrefs();
      this._initTheme();
      this._applyDensity();
      await Promise.all([this.loadHealth(), this.loadSessions(), this.loadStats(), this.loadConfig()]);
      // Prime the forecast card so it has data the first time the user opens Insights.
      this.loadForecast();
      this.loadHourlyCost(168);
      this.connectSSE();
      this._startNowTimer();
      this._installKeydown();
      setInterval(() => this.loadStats(), 5000);
      setInterval(() => this.loadHealth(), 30000);
      // Watch insights view
      this.$watch && this.$watch('view', (v) => this._onViewChange(v));
      // Trigger initial tile polling if persisted as on
      this._restartTileTimer();
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
        // Prefer the new "theme" key (light | dark | auto). Fall back to
        // the legacy "appearance" key (light | dark) so users upgrading
        // from the previous build keep their pick.
        const t = localStorage.getItem("claudewatch.theme");
        if (t === "light" || t === "dark" || t === "auto") {
          this.theme = t;
        } else {
          const legacy = localStorage.getItem("claudewatch.appearance");
          if (legacy === "light" || legacy === "dark") this.theme = legacy;
        }
        // Keep `appearance` aligned for back-compat consumers. "auto"
        // resolves via _applyTheme below.
        if (this.theme === "light" || this.theme === "dark") this.appearance = this.theme;
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
      try {
        const tm = localStorage.getItem("claudewatch.tileMode");
        if (tm === "1" || tm === "true") this.tileMode = true;
      } catch (e) { /* ignore */ }
    },

    saveTileMode() {
      try { localStorage.setItem("claudewatch.tileMode", this.tileMode ? "1" : "0"); }
      catch (e) { /* ignore */ }
      // Restart polling for the current visibility
      this._restartTileTimer();
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
    /**
     * Cycle through the three theme states: light → dark → auto → light.
     * Triggered by the header toggle button.
     */
    cycleTheme() {
      const next = this.theme === "light" ? "dark"
                 : this.theme === "dark"  ? "auto"
                 : "light";
      this.setTheme(next);
    },
    /**
     * Set the theme to one of "light" | "dark" | "auto". Unknown values
     * fall back to "auto". Persists to localStorage and re-applies.
     */
    setTheme(theme) {
      if (theme !== "light" && theme !== "dark" && theme !== "auto") {
        theme = "auto";
      }
      this.theme = theme;
      try { localStorage.setItem("claudewatch.theme", theme); } catch (e) { /* ignore */ }
      // Mirror to legacy key so older code paths (and anyone inspecting
      // the localStorage in DevTools) stay coherent.
      try {
        if (theme === "light" || theme === "dark") {
          localStorage.setItem("claudewatch.appearance", theme);
        }
      } catch (e) { /* ignore */ }
      this._applyTheme();
    },
    /**
     * Backwards-compatible tooltip text for the theme toggle. The header
     * UI shows the icon for the *current* state; the tooltip tells the
     * user what clicking will do next.
     */
    themeTooltip() {
      if (this.theme === "light") return "Theme: Light (click for Dark)";
      if (this.theme === "dark")  return "Theme: Dark (click for Auto)";
      return "Theme: Auto / system (click for Light)";
    },
    /**
     * Apply the current `theme` to <html>. In "auto" mode, defer to the
     * cached MediaQueryList (set by _initTheme). Also keeps the
     * `appearance` field aligned with the resolved value for any code
     * that still reads it.
     */
    _applyTheme() {
      const wantDark = this.theme === "dark"
        || (this.theme === "auto" && !!(this._themeMql && this._themeMql.matches));
      const html = document.documentElement;
      const body = document.body;
      if (html && html.classList) {
        html.classList.toggle("dark", wantDark);
        html.classList.toggle("light", !wantDark);
      }
      if (body && body.classList) {
        body.classList.toggle("dark", wantDark);
        body.classList.toggle("light", !wantDark);
      }
      this.appearance = wantDark ? "dark" : "light";
    },
    /**
     * Subscribe to OS-level prefers-color-scheme changes and do the
     * first paint. Called once from init().
     */
    _initTheme() {
      try {
        if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
          this._themeMql = window.matchMedia("(prefers-color-scheme: dark)");
          const onChange = () => this._applyTheme();
          // Modern API; .addListener is the deprecated fallback.
          if (typeof this._themeMql.addEventListener === "function") {
            this._themeMql.addEventListener("change", onChange);
          } else if (typeof this._themeMql.addListener === "function") {
            this._themeMql.addListener(onChange);
          }
        }
      } catch (e) { /* ignore */ }
      this._applyTheme();
    },
    // Legacy shim: the previous build called saveAppearance() from a
    // settings radio. Tests and any deep-link code may still call it.
    // Forward to setTheme() so behavior stays consistent.
    saveAppearance() {
      this.setTheme(this.appearance);
    },
    saveDensity() {
      try { localStorage.setItem("claudewatch.density", this.density); }
      catch (e) { /* ignore */ }
      this._applyDensity();
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
          this._normalizeConfig();
          this.chatRemoteEnabled = !!(this.config.remote_control && this.config.remote_control.enabled);
          this._syncConfigDraft();
          return;
        }
      } catch (e) { /* ignore */ }
      this._setError(`Failed to load /api/config: HTTP ${r?.status ?? '???'}`);
    },
    _syncConfigDraft() {
      this.configDraft = JSON.parse(JSON.stringify(this.config));
      this._normalizeConfigDraft();
      this.configDirty = false;
    },
    _normalizeConfigDraft() {
      if (!this.configDraft.notifications) this.configDraft.notifications = {};
      if (!this.configDraft.remote_control) this.configDraft.remote_control = { enabled: false };
      if (!this.configDraft.plan) this.configDraft.plan = "api";
      if (!this.configDraft.editor) this.configDraft.editor = { enabled: false, command: "code" };
      if (typeof this.configDraft.editor.enabled !== "boolean") this.configDraft.editor.enabled = false;
      if (!this.configDraft.editor.command) this.configDraft.editor.command = "code";
    },
    markConfigDirty() {
      this.configDirty = JSON.stringify(this.configDraft) !== JSON.stringify(this.config);
      // Reset transient status when user edits again after a save/error
      if (this.configSavingState === "saved" || this.configSavingState === "error") {
        this.configSavingState = "idle";
        this.configSaveError = "";
      }
    },
    revertConfig() {
      this.configDraft = JSON.parse(JSON.stringify(this.config));
      this._normalizeConfigDraft();
      this.configDirty = false;
      this.configSaveError = "";
      this.configSavingState = "idle";
    },
    async saveConfigDraft() {
      if (!this.configDirty) return;
      this.configSavingState = "saving";
      this.configSaveError = "";
      try {
        const r = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.configDraft),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${r.status}`);
        }
        this.config = await r.json();
        this._normalizeConfig();
        this.chatRemoteEnabled = !!(this.config.remote_control && this.config.remote_control.enabled);
        this._syncConfigDraft();
        this.configSavingState = "saved";
        setTimeout(() => { if (this.configSavingState === "saved") this.configSavingState = "idle"; }, 2000);
      } catch (e) {
        this.configSavingState = "error";
        this.configSaveError = String(e.message || e);
      }
    },
    switchView(target) {
      if (this.view === "settings" && this.configDirty) {
        if (!confirm("You have unsaved settings changes. Discard them?")) return;
        this.revertConfig();
      }
      this.view = target;
    },
    _normalizeConfig() {
      if (!this.config.notifications) this.config.notifications = {};
      if (!this.config.remote_control) this.config.remote_control = { enabled: false };
      if (!this.config.plan) this.config.plan = "api";
      if (!this.config.editor) this.config.editor = { enabled: false, command: "code" };
      if (typeof this.config.editor.enabled !== "boolean") this.config.editor.enabled = false;
      if (!this.config.editor.command) this.config.editor.command = "code";
    },
    showCost() {
      return (this.config?.plan ?? "api") === "api";
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
          this._normalizeConfig();
          this.chatRemoteEnabled = !!(this.config.remote_control && this.config.remote_control.enabled);
          // If no user-staged edits are pending, keep draft in lockstep so
          // the pricing inline-edit (and similar bypass paths) don't
          // accidentally show a phantom "Unsaved changes" badge.
          if (!this.configDirty) this._syncConfigDraft();
        }
      } catch (e) { console.warn("save config failed", e); }
    },
    async saveEditorConfig() {
      const e = this.config.editor || { enabled: false, command: "code" };
      await this.saveConfig({ editor: {
        enabled: !!e.enabled,
        command: (e.command || "code").trim() || "code",
      }});
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
      // Keep draft pricing in lockstep so the dirty check doesn't trip
      if (this.configDraft) this.configDraft.pricing = JSON.parse(JSON.stringify(next));
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

    // Cost forecast (Insights tab)
    async loadForecast() {
      let r;
      try {
        r = await fetch("/api/forecast?window_hours=24");
        if (r && r.ok) {
          const data = await r.json();
          if (data && typeof data === "object") {
            this.forecastData = data;
          }
          return;
        }
      } catch (e) {
        // Silent — the card simply shows a placeholder until the next refresh.
      }
    },
    showForecastCard() {
      // Plan-gate: only API users see $ amounts. Plus an explicit card toggle.
      return this.showCost() && this.cardVisibility.cost !== false;
    },

    // Hourly cost trend (Insights tab) — bar chart of last 7d of ended-session cost.
    async loadHourlyCost(hours = 168) {
      const h = Number(hours) || 168;
      let r;
      try {
        r = await fetch(`/api/history/hourly-cost?hours=${h}`);
        if (r && r.ok) {
          const data = await r.json();
          if (data && typeof data === "object") {
            this.hourlyCostData = {
              hours: Number(data.hours) || h,
              bins: Array.isArray(data.bins) ? data.bins : [],
              total_cost_usd: Number(data.total_cost_usd) || 0,
            };
            this.hourlyCostError = null;
            this.$nextTick && this.$nextTick(() => this._renderHourlyCostChart());
            setTimeout(() => this._renderHourlyCostChart(), 50);
          }
          return;
        }
        this.hourlyCostError = `Failed to load /api/history/hourly-cost: HTTP ${r?.status ?? "???"}`;
      } catch (e) {
        this.hourlyCostError = "Network error loading hourly cost";
      }
    },
    _renderHourlyCostChart() {
      const canvas = document.getElementById("hourly-cost-bar");
      if (!canvas) return;
      const bins = (this.hourlyCostData && this.hourlyCostData.bins) || [];
      // Label by hour-of-day with a "MM-DD HH" hint every Nth tick (the
      // drawBarChart label stride keeps it readable). Keep the label compact
      // so the existing axis renderer doesn't run out of room.
      const data = bins.map((b) => {
        const iso = b.hour_start || "";
        // ISO is "YYYY-MM-DDTHH:00:00+00:00" → "MM-DD HH" is informative
        // without overflowing the tick width.
        const md = iso.slice(5, 10);
        const hh = iso.slice(11, 13);
        return {
          label: hh ? `${md} ${hh}` : iso,
          value: Number(b.cost_usd) || 0,
        };
      });
      this.drawBarChart(canvas, data);
    },
    hourlyCostSummary() {
      const d = this.hourlyCostData;
      if (!d || !Array.isArray(d.bins)) return "";
      const total = Number(d.total_cost_usd) || 0;
      const hours = Number(d.hours) || d.bins.length;
      const sessions = d.bins.reduce((s, b) => s + (Number(b.session_count) || 0), 0);
      const sessLabel = `${sessions} session${sessions === 1 ? "" : "s"}`;
      return `Total: ${this.fmtMoney(total)} over ${hours} hours · ${sessLabel}`;
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
      if (v === "settings" && !this.configDirty) {
        // Re-sync draft when entering Settings so it reflects any external
        // changes that happened while we were away.
        this._syncConfigDraft();
      }
      if (v === "insights") {
        this.loadInsights();
        this.loadForecast();
        this.loadHourlyCost(168);
        if (this._insightsTimer) clearInterval(this._insightsTimer);
        this._insightsTimer = setInterval(() => {
          this.loadInsights();
          this.loadForecast();
          this.loadHourlyCost(168);
        }, 30000);
      } else {
        if (this._insightsTimer) { clearInterval(this._insightsTimer); this._insightsTimer = null; }
      }
      if (v === "history") {
        this.loadHistory();
        this.loadHourlyHistory(24);
      }
      if (v === "status") {
        this._startAdminPolling();
      } else {
        this._stopAdminPolling();
      }
      if (v === "files") {
        this.loadFileChanges();
        if (this._filesTimer) clearInterval(this._filesTimer);
        this._filesTimer = setInterval(() => this.loadFileChanges(), 5000);
      } else {
        if (this._filesTimer) { clearInterval(this._filesTimer); this._filesTimer = null; }
      }
      // Restart tile polling depending on dashboard+tile mode
      this._restartTileTimer();
    },

    // --- Files tab ---
    async loadFileChanges() {
      const mins = Number(this.filesMinutes) || 10;
      let r;
      try {
        r = await fetch(`/api/file-changes?minutes=${mins}`);
        if (r.status === 404) {
          this.fileChangesUnavailable = true;
          this.fileChanges = [];
          this.filesLastRefresh = new Date().toISOString();
          return;
        }
        if (r.ok) {
          const data = await r.json();
          this.fileChanges = Array.isArray(data) ? data : (data.changes || []);
          this.fileChangesUnavailable = false;
          this.fileChangesError = null;
          this.filesLastRefresh = new Date().toISOString();
          return;
        }
      } catch (e) {
        this.fileChangesError = "Network error loading file changes";
        return;
      }
      this.fileChangesError = `Failed to load /api/file-changes: HTTP ${r?.status ?? '???'}`;
    },
    visibleFileChanges() {
      const q = (this.filesSearch || "").trim().toLowerCase();
      const kind = this.filesKindFilter;
      const pidFilter = this.filesPidFilter;
      let arr = [...(this.fileChanges || [])];
      if (kind && kind !== "All") {
        const want = kind.toLowerCase();
        arr = arr.filter((c) => (c.kind || "").toLowerCase() === want);
      }
      if (q) {
        arr = arr.filter((c) => {
          const hay = `${c.path || ""} ${c.abs_path || ""} ${c.project || ""} ${c.cwd || ""}`.toLowerCase();
          return hay.includes(q);
        });
      }
      if (pidFilter != null) {
        arr = arr.filter((c) => Array.isArray(c.session_pids) && c.session_pids.includes(pidFilter));
      }
      arr.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
      return arr;
    },
    fileChangeKindIcon(kind) {
      const k = (kind || "").toLowerCase();
      if (k === "created" || k === "added") return "+";
      if (k === "deleted" || k === "removed") return "−";
      if (k === "modified" || k === "changed") return "✎";
      return "•";
    },
    fileChangeKindColor(kind) {
      const k = (kind || "").toLowerCase();
      if (k === "created" || k === "added") return "text-emerald-300";
      if (k === "deleted" || k === "removed") return "text-rose-300";
      if (k === "modified" || k === "changed") return "text-amber-300";
      return "text-zinc-400";
    },
    selectFileChange(change) {
      if (!change) return;
      this.filesSelected = { cwd: change.cwd, path: change.path, project: change.project, abs_path: change.abs_path };
      this.loadDiff();
    },
    closeFileDiff() {
      this.filesSelected = null;
      this.filesDiff = null;
      this.filesDiffError = null;
      this._editorOpenStatus = null;
    },
    async loadDiff() {
      if (!this.filesSelected) return;
      this.filesDiffLoading = true;
      this.filesDiff = null;
      this.filesDiffError = null;
      const q = new URLSearchParams({
        cwd: this.filesSelected.cwd || "",
        path: this.filesSelected.path || "",
        context: "3",
      });
      let r;
      try {
        r = await fetch(`/api/files/diff?${q.toString()}`);
        if (r.status === 404) {
          this.filesDiffError = "Diff endpoint not available yet (backend not merged).";
          this.filesDiffLoading = false;
          return;
        }
        if (r.ok) {
          this.filesDiff = await r.json();
          this.filesDiffLoading = false;
          return;
        }
      } catch (e) {
        this.filesDiffError = "Network error loading diff";
        this.filesDiffLoading = false;
        return;
      }
      this.filesDiffError = `Failed to load diff: HTTP ${r?.status ?? '???'}`;
      this.filesDiffLoading = false;
    },
    diffLineClass(line) {
      if (line.startsWith("+++") || line.startsWith("---")) return "text-zinc-400";
      if (line.startsWith("@@")) return "text-cyan-300";
      if (line.startsWith("+")) return "text-emerald-300";
      if (line.startsWith("-")) return "text-rose-300";
      return "text-zinc-300";
    },
    diffLines(diff) {
      if (!diff) return [];
      return String(diff).split("\n");
    },
    async openInEditor() {
      if (!this.filesSelected) return;
      if (!(this.config.editor && this.config.editor.enabled)) {
        this._editorOpenStatus = "Enable 'Open in editor' in Settings → Editor first.";
        return;
      }
      this._editorOpenStatus = "Opening…";
      let r;
      try {
        r = await fetch("/api/files/open", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cwd: this.filesSelected.cwd, path: this.filesSelected.path }),
        });
      } catch (e) {
        this._editorOpenStatus = "Network error";
        return;
      }
      if (r.status === 404) {
        this._editorOpenStatus = "Open-in-editor endpoint not available yet.";
        return;
      }
      if (r.ok) {
        this._editorOpenStatus = "Opened";
        setTimeout(() => { if (this._editorOpenStatus === "Opened") this._editorOpenStatus = null; }, 2000);
        return;
      }
      this._editorOpenStatus = `Failed: HTTP ${r.status}`;
    },
    jumpToFilesForSession(sess) {
      this.filesPidFilter = sess.pid;
      this.filesSearch = "";
      this.filesKindFilter = "All";
      this.switchView("files");
    },

    // --- Tile mode ---
    _restartTileTimer() {
      if (this._tileTimer) { clearInterval(this._tileTimer); this._tileTimer = null; }
      if (this.view === "dashboard" && this.tileMode) {
        this.refreshTilePreviews();
        this._tileTimer = setInterval(() => this.refreshTilePreviews(), 5000);
      }
    },
    tileSessions() {
      const all = this.visibleSessions();
      // Cap at 6 for performance
      return all.slice(0, 6);
    },
    async refreshTilePreviews() {
      const tiles = this.tileSessions();
      const pids = tiles.map((s) => s.pid);
      // Purge stale
      for (const pid of Object.keys(this.tilePreviews)) {
        if (!pids.includes(Number(pid))) delete this.tilePreviews[pid];
      }
      await Promise.all(pids.map((pid) => this._fetchTilePreview(pid)));
    },
    async _fetchTilePreview(pid) {
      let r;
      try {
        r = await fetch(`/api/sessions/${pid}/log-tail?limit=6`);
        if (r.ok) {
          const data = await r.json();
          const entries = Array.isArray(data) ? data : (data.entries || []);
          this.tilePreviews[pid] = { entries, error: null };
          return;
        }
      } catch (e) {
        this.tilePreviews[pid] = { entries: [], error: "network" };
        return;
      }
      this.tilePreviews[pid] = { entries: [], error: `HTTP ${r?.status ?? '???'}` };
    },
    tilePreviewEntries(pid) {
      const p = this.tilePreviews[pid];
      if (!p) return [];
      return p.entries || [];
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

    // --- Admin / Status tab ---
    async loadAdminStatus() {
      let r;
      try {
        r = await fetch("/api/admin/status");
        if (r.ok) {
          this.adminStatus = await r.json();
          this.adminStatusError = null;
          return;
        }
      } catch (e) {
        this.adminStatusError = "Network error loading status";
        return;
      }
      this.adminStatusError = `Failed to load /api/admin/status: HTTP ${r?.status ?? '???'}`;
    },
    async loadAdminLogs() {
      const lines = Number(this.adminLogsLineCount) || 200;
      const grep = (this.adminLogsGrep || "").trim();
      const params = new URLSearchParams({ lines: String(lines) });
      if (grep) params.set("grep", grep);
      this.adminLogsLoading = true;
      let r;
      try {
        r = await fetch(`/api/admin/logs?${params.toString()}`);
        if (r.ok) {
          this.adminLogs = await r.json();
          this.adminLogsError = null;
          this.adminLogsLoading = false;
          // Scroll to bottom for tail UX
          this.$nextTick && this.$nextTick(() => {
            const pre = document.getElementById("admin-log-view");
            if (pre) pre.scrollTop = pre.scrollHeight;
          });
          return;
        }
      } catch (e) {
        this.adminLogsError = "Network error loading logs";
        this.adminLogsLoading = false;
        return;
      }
      this.adminLogsError = `Failed to load /api/admin/logs: HTTP ${r?.status ?? '???'}`;
      this.adminLogsLoading = false;
    },
    onAdminLogsGrepInput(value) {
      this.adminLogsGrep = value;
      if (this._adminLogsGrepDebounce) clearTimeout(this._adminLogsGrepDebounce);
      this._adminLogsGrepDebounce = setTimeout(() => this.loadAdminLogs(), 250);
    },
    async loadMetrics() {
      let r;
      try {
        r = await fetch("/api/metrics");
        if (r.ok) {
          this.metricsData = await r.json();
          this.metricsError = null;
          return;
        }
      } catch (e) {
        this.metricsError = "Network error loading metrics";
        return;
      }
      this.metricsError = `Failed to load /api/metrics: HTTP ${r?.status ?? '???'}`;
    },
    onAdminLogsLineCountChange() {
      this.loadAdminLogs();
    },
    async loadHourlyHistory(hours) {
      const h = Number(hours) || 24;
      let r;
      try {
        r = await fetch(`/api/history/hourly?hours=${h}`);
        if (r.ok) {
          const data = await r.json();
          this.hourlyHistory = data && data.bins ? data : { bins: [] };
          this.$nextTick && this.$nextTick(() => this._renderHourlyHistoryChart());
          setTimeout(() => this._renderHourlyHistoryChart(), 50);
          return;
        }
      } catch (e) {
        // silent — chart just shows "No data"
      }
    },
    _renderHourlyHistoryChart() {
      const canvas = document.getElementById("history-bar");
      if (!canvas) return;
      const bins = (this.hourlyHistory && this.hourlyHistory.bins) || [];
      const data = bins.map((b) => ({
        label: (b.hour || "").slice(11, 13) + "h",
        value: Number(b.sessions_started) || 0,
      }));
      this._drawCountBarChart(canvas, data);
    },
    _drawCountBarChart(canvas, data) {
      // Like drawBarChart but for integer counts (no "$" prefix on Y axis).
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      const W = Math.max(200, Math.floor(rect.width));
      const H = Math.max(120, Math.floor(rect.height || 160));
      canvas.width = W * dpr; canvas.height = H * dpr;
      canvas.style.width = W + "px"; canvas.style.height = H + "px";
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, W, H);
      const light = this.appearance === "light";
      const axisColor = light ? "#52525b" : "#a1a1aa";
      const gridColor = light ? "#e4e4e7" : "#27272a";
      const barColor = "#3b82f6";
      const padL = 30, padR = 8, padT = 10, padB = 22;
      const innerW = W - padL - padR, innerH = H - padT - padB;
      const max = Math.max(1, ...data.map((d) => d.value));
      ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
      ctx.fillStyle = axisColor; ctx.font = "10px ui-sans-serif, -apple-system";
      for (let i = 0; i <= 4; i++) {
        const y = padT + (innerH * i) / 4;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        const v = Math.round(max * (1 - i / 4));
        ctx.fillText(String(v), 2, y + 3);
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
      ctx.fillStyle = axisColor;
      const stride = Math.max(1, Math.ceil(n / 8));
      data.forEach((d, i) => {
        if (i % stride !== 0) return;
        const x = padL + i * (barW + gap) + barW / 2;
        const label = d.label || "";
        const w = ctx.measureText(label).width;
        ctx.fillText(label, x - w / 2, H - 6);
      });
    },
    _startAdminPolling() {
      // Initial fetches
      this.loadAdminStatus();
      this.loadAdminLogs();
      this.loadMetrics();
      if (this._adminPollTimer) return;
      this._adminPollTimer = setInterval(() => {
        if (this.view !== "status") return;
        this.loadAdminStatus();
        this.loadMetrics();
        if (this.adminLogsTail) this.loadAdminLogs();
      }, 5000);
    },
    _stopAdminPolling() {
      if (this._adminPollTimer) {
        clearInterval(this._adminPollTimer);
        this._adminPollTimer = null;
      }
    },
    async restartDaemon() {
      this.restartConfirmOpen = false;
      this.restartState = "restarting";
      this._setError("Backend restarting… reconnecting");
      try {
        await fetch("/api/admin/restart?confirm=true", { method: "POST" });
      } catch (e) {
        // Even if the connection tears mid-flight that's expected — keep polling.
      }
      // Poll /api/health up to ~10s waiting for the daemon to come back.
      let ok = false;
      for (let i = 0; i < 50; i++) {
        await new Promise((r) => setTimeout(r, 200));
        try {
          const r = await fetch("/api/health");
          if (r && r.ok) { ok = true; break; }
        } catch (e) { /* still restarting */ }
      }
      this.restartState = "idle";
      if (ok) {
        this.errorBanner = null;
        this.loadAdminStatus();
        this.loadAdminLogs();
      } else {
        this._setError("Restart timed out waiting for daemon to come back");
      }
    },
    requestRestart() {
      this.restartConfirmOpen = true;
    },
    async pruneNow(hours) {
      const h = Number(hours) || 48;
      if (!confirm(`Delete sessions older than ${h}h from the history DB?`)) return;
      let r;
      try {
        r = await fetch(`/api/admin/prune?hours=${h}`, { method: "POST" });
      } catch (e) {
        alert("Prune failed: " + e);
        return;
      }
      if (!r.ok) {
        let detail = "";
        try { const j = await r.json(); detail = j.detail || ""; } catch (e) { /* ignore */ }
        alert(`Prune failed: HTTP ${r.status}${detail ? " · " + detail : ""}`);
        return;
      }
      const data = await r.json().catch(() => ({}));
      const n = data && typeof data.rows_deleted === "number" ? data.rows_deleted : 0;
      this._adminPostToast = `Pruned ${n} row${n === 1 ? "" : "s"}`;
      setTimeout(() => { this._adminPostToast = null; }, 3000);
      // Refresh dependent views
      this.loadAdminStatus();
      this.loadHistory();
      this.loadHourlyHistory(24);
    },

    // --- History tab enrichments ---
    historyKey(h) {
      if (!h) return "";
      return `${h.pid}|${h.started_at || ""}`;
    },
    toggleHistoryRow(h) {
      const k = this.historyKey(h);
      this.historyExpandedKeys[k] = !this.historyExpandedKeys[k];
    },
    isHistoryRowExpanded(h) {
      return !!this.historyExpandedKeys[this.historyKey(h)];
    },
    historySummaryJson(h) {
      try {
        return JSON.stringify(h && h.summary != null ? h.summary : h, null, 2);
      } catch (e) {
        return "{}";
      }
    },
    visibleHistory() {
      const arr = Array.isArray(this.history) ? [...this.history] : [];
      const now = Date.now();
      const oneDay = 24 * 3600 * 1000;
      const oneWeek = 7 * oneDay;
      switch (this.historyFilter) {
        case "today":
          return arr.filter((h) => {
            const t = h.ended_at ? new Date(h.ended_at).getTime() : 0;
            return t && now - t <= oneDay;
          });
        case "week":
          return arr.filter((h) => {
            const t = h.ended_at ? new Date(h.ended_at).getTime() : 0;
            return t && now - t <= oneWeek;
          });
        case "high-cost":
          return arr.filter((h) => Number(h.cost_estimate || 0) >= 1.0);
        default:
          return arr;
      }
    },
    historyFmtBytes(n) {
      const v = Number(n) || 0;
      if (v < 1024) return v + " B";
      if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KB";
      if (v < 1024 * 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + " MB";
      return (v / 1024 / 1024 / 1024).toFixed(2) + " GB";
    },
    adminLogLineClass(line) {
      if (!line) return "text-zinc-400";
      const s = String(line);
      if (/\bERROR\b|\bCRITICAL\b/.test(s)) return "text-rose-300";
      if (/\bWARN(?:ING)?\b/.test(s)) return "text-amber-300";
      if (/\bDEBUG\b/.test(s)) return "text-zinc-500";
      return "text-zinc-300";
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
      this.switchView("dashboard");
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
        if (e.key === "i") { this.switchView("insights"); this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "d") { this.switchView("dashboard"); this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "f") { this.switchView("files"); this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "h") {
          this.switchView("history");
          if (this.view === "history") this.loadHistory();
          this._gPressed = false; e.preventDefault(); return;
        }
        if (e.key === "s") { this.switchView("settings"); this._gPressed = false; e.preventDefault(); return; }
        if (e.key === "t") { this.switchView("status"); this._gPressed = false; e.preventDefault(); return; }
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

// For testing — exposes appRoot to Vitest. Browser ignores this; Alpine
// still picks up appRoot via the global function declaration above.
if (typeof globalThis !== "undefined") {
  globalThis.__claudewatchAppRoot = appRoot;
}
