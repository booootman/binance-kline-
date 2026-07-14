// assets/charts.js — Binance futures command dashboard logic (per-symbol view)
(function () {
  'use strict';
  var DATA = window.MARKET_DATA || [];   // static fallback from data.js
  var GEN = window.GENERATED_AT || '';    // static fallback
  var LIVE = false;                       // true once live data loaded
  var currentSymbol = '';                 // active symbol for single-symbol view
  var realtimeSource = null;
  var realtimeKey = '';
  var AUTO_STRATEGY_REFRESH_MS = 5 * 60 * 1000;
  var clockTimer = null;
  var strategyRefreshTimer = null;
  var strategyRefreshBusy = false;
  var nextStrategyRefreshAt = 0;
  var lastGuardRenderAt = 0;
  var MARKET_STATE = {
    stale: DATA.length > 0,
    warning: DATA.length > 0 ? '当前为内置离线快照，等待服务端行情' : '',
    errorType: DATA.length > 0 ? 'static_fallback' : ''
  };
  var CURRENT_USER = null;
  var SIGNAL_REVIEW_STATE = { key: '', payload: null, loading: false, error: '', lastFetch: 0 };
  var SIGNAL_REVIEW_REQUEST_SEQ = 0;
  var STORAGE_KEYS = {
    signalHistory: 'bian_dashboard_signal_history',
    symbolHistory: 'bian_dashboard_symbol_history',
    positionState: 'bian_dashboard_position_state',
    accountRisk: 'bian_dashboard_account_risk',
    tvInterval: 'bian_dashboard_tv_kline_interval',
    signalAlert: 'bian_dashboard_signal_alert_pref',
    customSymbols: 'bian_dashboard_custom_syms',
    removedSymbols: 'bian_dashboard_removed_syms'
  };
  var SIGNAL_HISTORY_KEY = STORAGE_KEYS.signalHistory;
  var SYMBOL_HISTORY_KEY = STORAGE_KEYS.symbolHistory;
  var POSITION_STATE_KEY = STORAGE_KEYS.positionState;
  var ACCOUNT_RISK_KEY = STORAGE_KEYS.accountRisk;
  var TV_KLINE_INTERVAL_KEY = STORAGE_KEYS.tvInterval;
  var SIGNAL_ALERT_PREF_KEY = STORAGE_KEYS.signalAlert;
  var LS_KEY = STORAGE_KEYS.customSymbols;
  var REMOVED_KEY = STORAGE_KEYS.removedSymbols;
  var tvKlineInterval = loadTvKlineInterval();
  var tvKlineKey = '';
  var signalAlertPref = loadSignalAlertPref();
  var signalAlertState = { lastKey: '', lastAt: 0, audioCtx: null };
  var pendingPreferencePatch = {};
  var preferenceSaveTimer = null;
  var root = getComputedStyle(document.documentElement);
  var C = {
    ink: root.getPropertyValue('--ink').trim(),
    muted: root.getPropertyValue('--muted').trim(),
    rule: root.getPropertyValue('--rule').trim(),
    bg2: root.getPropertyValue('--bg2').trim(),
    accent: root.getPropertyValue('--accent').trim(),
    accent2: root.getPropertyValue('--accent2').trim(),
    bull: root.getPropertyValue('--bull').trim(),
    bear: root.getPropertyValue('--bear').trim(),
    warn: root.getPropertyValue('--warn').trim()
  };
  var TFS = ['1m', '5m', '15m', '1h', '4h', '8h', '1d'];
  var FONT = "'GeistMono','Microsoft YaHei',sans-serif";
  var charts = [];

  function scopeLocalStorageKeys() {
    var userId = CURRENT_USER && CURRENT_USER.id != null ? String(CURRENT_USER.id) : 'local';
    var suffix = ':user:' + userId;
    function scoped(base) { return base + suffix; }
    if (userId === '0' || userId === 'local') {
      Object.keys(STORAGE_KEYS).forEach(function (name) {
        var base = STORAGE_KEYS[name];
        var target = scoped(base);
        try {
          if (localStorage.getItem(target) == null && localStorage.getItem(base) != null) {
            localStorage.setItem(target, localStorage.getItem(base));
          }
        } catch (e) {}
      });
    }
    SIGNAL_HISTORY_KEY = scoped(STORAGE_KEYS.signalHistory);
    SYMBOL_HISTORY_KEY = scoped(STORAGE_KEYS.symbolHistory);
    POSITION_STATE_KEY = scoped(STORAGE_KEYS.positionState);
    ACCOUNT_RISK_KEY = scoped(STORAGE_KEYS.accountRisk);
    TV_KLINE_INTERVAL_KEY = scoped(STORAGE_KEYS.tvInterval);
    SIGNAL_ALERT_PREF_KEY = scoped(STORAGE_KEYS.signalAlert);
    LS_KEY = scoped(STORAGE_KEYS.customSymbols);
    REMOVED_KEY = scoped(STORAGE_KEYS.removedSymbols);
    tvKlineInterval = loadTvKlineInterval();
    signalAlertPref = loadSignalAlertPref();
  }

  /* ---------- helpers ---------- */
  function fmtPrice(v) {
    if (v >= 1) return v.toFixed(4);
    if (v >= 0.01) return v.toFixed(5);
    if (v >= 0.0001) return v.toFixed(6);
    return v.toFixed(8);
  }
  function fmtVol(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(0) + 'K';
    return v.toFixed(0);
  }
  function fmtPct(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
  function htmlSafe(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }
  function loadSignalAlertPref() {
    try {
      var raw = localStorage.getItem(SIGNAL_ALERT_PREF_KEY);
      var pref = raw ? JSON.parse(raw) : {};
      return { enabled: !!pref.enabled };
    } catch (e) {
      return { enabled: false };
    }
  }
  function saveSignalAlertPref() {
    try { localStorage.setItem(SIGNAL_ALERT_PREF_KEY, JSON.stringify(signalAlertPref)); } catch (e) {}
  }
  function alertButtonText() {
    return signalAlertPref.enabled ? 'ALERT ON' : 'ALERT OFF';
  }
  function updateSignalAlertButton() {
    var btn = document.querySelector('#signal-banner .js-signal-alert-toggle');
    if (!btn) return;
    btn.textContent = alertButtonText();
    btn.className = 'alert-toggle js-signal-alert-toggle' + (signalAlertPref.enabled ? ' active' : '');
    btn.title = signalAlertPref.enabled ? 'Entry/stop sound alerts are on' : 'Entry/stop sound alerts are off';
  }
  function signalAlertKey(r, alert) {
    if (!r || !alert || !alert.text) return '';
    return [r.symbol || '', alert.cls || '', alert.text].join('|');
  }
  function playSignalBeep(kind) {
    var AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    try {
      var ctx = signalAlertState.audioCtx;
      if (!ctx) {
        ctx = new AudioContext();
        signalAlertState.audioCtx = ctx;
      }
      if (ctx.state === 'suspended') {
        var resumed = ctx.resume();
        if (resumed && typeof resumed.catch === 'function') resumed.catch(function () {});
      }
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      var now = ctx.currentTime;
      osc.type = 'sine';
      osc.frequency.value = kind === 'danger' ? 740 : 520;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(kind === 'danger' ? 0.09 : 0.055, now + 0.025);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.24);
    } catch (e) {}
  }
  function maybeNotifySignalAlert(r, alert) {
    if (!signalAlertPref.enabled || !alert || !alert.text) return;
    if (alert.cls !== 'danger' && alert.cls !== 'watch') return;
    var key = signalAlertKey(r, alert);
    var now = Date.now();
    var cooldown = alert.cls === 'danger' ? 30000 : 60000;
    if (key && signalAlertState.lastKey === key && now - signalAlertState.lastAt < cooldown) return;
    signalAlertState.lastKey = key;
    signalAlertState.lastAt = now;
    playSignalBeep(alert.cls);
  }
  function biasClass(b) {
    if (b.indexOf('观望') >= 0) return 'wait';
    if (b.indexOf('偏多') >= 0) return 'bull';
    if (b.indexOf('偏空') >= 0) return 'bear';
    return 'wait';
  }
  function biasBadge(b) {
    var c = biasClass(b);
    return '<span class="bias-badge b-' + c + '">' + b + '</span>';
  }
  // P0: translate bias to plain action
  function biasPlain(b) {
    if (b.indexOf('观望') >= 0) return '先观察，不开新仓';
    if (b.indexOf('偏多') >= 0) return '倾向做多（买入）';
    if (b.indexOf('偏空') >= 0) return '倾向做空（卖出）';
    return '先别动，等信号';
  }
  // P0: confidence → suggested position size
  function confToPosition(c) {
    if (c >= 80) return { stars: '★★★', text: '可半仓', color: C.bull };
    if (c >= 60) return { stars: '★★', text: '轻仓试水', color: C.accent2 };
    if (c >= 40) return { stars: '★', text: '观望为主', color: C.warn };
    return { stars: '☆', text: '别碰', color: C.bear };
  }
  function fmtSizePct(v) {
    var n = Number(v);
    if (!isFinite(n) || n <= 0) return '0%';
    return (n >= 10 ? n.toFixed(0) : n.toFixed(1)) + '%';
  }
  function sizingOf(advice, report) {
    if (advice && advice.risk_sizing) return advice.risk_sizing;
    if (report && report.signal_quality && report.signal_quality.risk_sizing) return report.signal_quality.risk_sizing;
    return null;
  }
  function positionFromSizing(sizing, confidence) {
    var fuse = accountFuseStatus();
    if (fuse.active) return { stars: '', text: '0%', sizePct: 0, color: C.bear, note: fuse.text };
    if (!sizing) {
      return { stars: '', text: '0%', sizePct: 0, color: C.warn, note: '后端未返回风险预算仓位' };
    }
    var pct = Number(sizing.suggested_size_pct) || 0;
    var allowed = sizing.allowed !== false && pct > 0;
    var color = allowed ? (pct >= 20 ? C.accent2 : pct >= 10 ? C.accent : C.warn) : C.bear;
    return {
      stars: '',
      text: fmtSizePct(pct),
      sizePct: pct,
      color: color,
      note: sizing.note || (allowed ? '风险预算仓位' : '当前不给仓位')
    };
  }
  function emtText(e) { return e === 'bull' ? '多' : e === 'bear' ? '空' : '震'; }
  function emtColor(e) { return e === 'bull' ? C.bull : e === 'bear' ? C.bear : C.warn; }
  function init(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    var c = echarts.init(el, null, { renderer: 'svg' });
    charts.push(c);
    return c;
  }
  function loadTvKlineInterval() {
    var allowed = ['1', '5', '15', '60', '240', 'D'];
    try {
      var saved = localStorage.getItem(TV_KLINE_INTERVAL_KEY);
      if (allowed.indexOf(saved) >= 0) return saved;
    } catch (e) {}
    return '15';
  }
  function saveTvKlineInterval(v) {
    try { localStorage.setItem(TV_KLINE_INTERVAL_KEY, v); } catch (e) {}
    saveServerPreferences({ tv_kline_interval: v });
  }
  function saveServerPreferences(patch) {
    if (!patch || typeof patch !== 'object') return;
    Object.keys(patch).forEach(function (key) { pendingPreferencePatch[key] = patch[key]; });
    if (preferenceSaveTimer) clearTimeout(preferenceSaveTimer);
    preferenceSaveTimer = setTimeout(flushServerPreferences, 350);
  }
  function flushServerPreferences() {
    var prefs = pendingPreferencePatch;
    pendingPreferencePatch = {};
    preferenceSaveTimer = null;
    if (!prefs || !Object.keys(prefs).length) return;
    fetch('api/preferences', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preferences: prefs }),
      cache: 'no-store'
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (payload) {
        if (!res.ok || payload.saved !== true) throw new Error(payload.error || ('HTTP ' + res.status));
      });
    }).catch(function (err) {
      Object.keys(prefs).forEach(function (key) {
        if (!Object.prototype.hasOwnProperty.call(pendingPreferencePatch, key)) pendingPreferencePatch[key] = prefs[key];
      });
      if (!preferenceSaveTimer) preferenceSaveTimer = setTimeout(flushServerPreferences, 5000);
      console.warn('[dashboard] preference sync failed:', err.message);
    });
  }
  function applyServerPreferences(prefs) {
    if (!prefs || typeof prefs !== 'object') return;
    try {
      var symbolHistorySeed = [];
      if (Array.isArray(prefs.symbol_history)) {
        symbolHistorySeed = symbolHistorySeed.concat(prefs.symbol_history);
      }
      if (Array.isArray(prefs.custom_symbols)) {
        var custom = [];
        prefs.custom_symbols.forEach(function (item) {
          var sym = normalizeClientSymbol(item);
          if (sym && custom.indexOf(sym) < 0) custom.push(sym);
        });
        localStorage.setItem(LS_KEY, JSON.stringify(custom));
        symbolHistorySeed = symbolHistorySeed.concat(custom);
      }
      if (Array.isArray(prefs.removed_symbols)) {
        var removed = [];
        prefs.removed_symbols.forEach(function (item) {
          var sym = normalizeClientSymbol(item);
          if (sym && removed.indexOf(sym) < 0) removed.push(sym);
        });
        localStorage.setItem(REMOVED_KEY, JSON.stringify(removed));
        symbolHistorySeed = symbolHistorySeed.concat(removed);
      }
      if (prefs.position_state && typeof prefs.position_state === 'object') {
        localStorage.setItem(POSITION_STATE_KEY, JSON.stringify(prefs.position_state));
      }
      if (prefs.account_risk && typeof prefs.account_risk === 'object') {
        localStorage.setItem(ACCOUNT_RISK_KEY, JSON.stringify(prefs.account_risk));
      }
      if (Array.isArray(prefs.signal_history)) {
        localStorage.setItem(SIGNAL_HISTORY_KEY, JSON.stringify(prefs.signal_history.slice(0, 80)));
        prefs.signal_history.forEach(function (item) {
          if (item && item.symbol) symbolHistorySeed.push(item.symbol);
        });
      }
      if (['1', '5', '15', '60', '240', 'D'].indexOf(String(prefs.tv_kline_interval || '')) >= 0) {
        tvKlineInterval = String(prefs.tv_kline_interval);
        localStorage.setItem(TV_KLINE_INTERVAL_KEY, tvKlineInterval);
      }
      if (symbolHistorySeed.length) rememberSymbols(symbolHistorySeed, true);
    } catch (e) {
      console.warn('[dashboard] apply server preferences failed:', e.message);
    }
  }
  function loadServerPreferences(done) {
    fetch('api/preferences', { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyServerPreferences(payload.preferences || {});
      })
      .catch(function (err) {
        console.warn('[dashboard] preference load failed, using browser localStorage:', err.message);
      })
      .then(done);
  }
  function tvIntervalLabel(v) {
    return v === '60' ? '1h' : v === '240' ? '4h' : v === 'D' ? '1D' : v + 'm';
  }
  function tradingViewSymbol(sym) {
    var clean = String(sym || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (!clean) return '';
    return 'BINANCE:' + clean + (clean.endsWith('USDT') ? '.P' : '');
  }
  function tradingViewUrl(tvSymbol) {
    return 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent(tvSymbol);
  }
  function binanceFuturesUrl(sym) {
    var clean = String(sym || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    return clean ? 'https://www.binance.com/zh-CN/futures/' + encodeURIComponent(clean) : '#';
  }
  function renderTradingViewEmpty(text) {
    var host = document.getElementById('tv-kline');
    if (!host) return;
    tvKlineKey = '';
    host.innerHTML = '<div class="tv-empty">' + text + '</div>';
  }
  function bindTradingViewToolbar() {
    var toolbar = document.getElementById('tv-kline-toolbar');
    if (!toolbar) return;
    var btns = toolbar.querySelectorAll('[data-tv-interval]');
    for (var i = 0; i < btns.length; i++) {
      var value = btns[i].getAttribute('data-tv-interval');
      btns[i].classList.toggle('active', value === tvKlineInterval);
      if (btns[i]._tvBound) continue;
      btns[i]._tvBound = true;
      btns[i].addEventListener('click', function () {
        var next = this.getAttribute('data-tv-interval') || '15';
        if (next === tvKlineInterval) return;
        tvKlineInterval = next;
        saveTvKlineInterval(next);
        tvKlineKey = '';
        renderTradingViewKline();
      });
    }
  }
  function bindLogout() {
    var btn = document.getElementById('logout-btn');
    if (!btn || btn._bound) return;
    btn._bound = true;
    btn.addEventListener('click', function () {
      fetch('api/logout', { method: 'POST', cache: 'no-store' })
        .catch(function () {})
        .then(function () { window.location.href = '/login'; });
    });
  }
  function setDiagnosticsMessage(text, cls) {
    var el = document.getElementById('diagnostics-message');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'password-message' + (cls ? ' ' + cls : '');
  }
  function closeDiagnosticsModal() {
    var modal = document.getElementById('diagnostics-modal');
    if (modal) {
      modal.classList.remove('show');
      modal.setAttribute('aria-hidden', 'true');
    }
    setDiagnosticsMessage('', '');
  }
  function diagStatus(value) {
    var cls = value === true ? 'good' : value === false ? 'bad' : 'warn';
    var text = value === true ? 'OK' : value === false ? 'FAIL' : '--';
    return '<span class="diag-pill ' + cls + '">' + text + '</span>';
  }
  function diagLine(label, value) {
    return '<div class="diag-line"><span>' + htmlSafe(label) + '</span><b>' + value + '</b></div>';
  }
  function diagCard(title, body) {
    return '<div class="diag-card"><h3>' + htmlSafe(title) + '</h3>' + body + '</div>';
  }
  function renderDiagnosticsPayload(payload) {
    var body = document.getElementById('diagnostics-body');
    if (!body) return;
    payload = payload || {};
    var cache = payload.cache || {};
    var analyzer = payload.analyzer || {};
    var realtime = payload.realtime || {};
    var storage = payload.storage || {};
    var mysql = storage.mysql || {};
    var redis = storage.redis || {};
    var auth = storage.auth || {};
    var review = payload.signal_review || {};
    var evaluator = review.evaluator || {};
    var sharing = realtime.sharing || {};
    var sharingCounts = sharing.counts || {};
    var sharingTotal = Number(sharing.total_requests || 0);
    var sharingReused = Number(sharing.reused_requests || 0);
    var sharingRate = Number(sharing.reuse_rate_pct || 0);
    var hubs = Array.isArray(realtime.hubs) ? realtime.hubs : [];
    var hubHtml = hubs.slice(0, 5).map(function (hub) {
      return '<div class="diag-line"><span>' + htmlSafe((hub.symbols || []).join(',') || hub.key || '-') + '</span><b>' +
        diagStatus(!hub.has_error && (hub.direct_connected || hub.latest_count > 0)) + '</b></div>' +
        (hub.error ? '<div class="diag-code">' + htmlSafe(hub.error) + '</div>' : '');
    }).join('') || '<div class="diag-line"><span>WebSocket hub</span><b>0</b></div>';
    body.innerHTML = '<div class="diagnostics-grid">' +
      diagCard('服务', [
        diagLine('状态', diagStatus(payload.ok !== false)),
        diagLine('运行', htmlSafe(String(payload.uptime_seconds || 0)) + 's'),
        diagLine('时间', htmlSafe(payload.time || '-'))
      ].join('')) +
      diagCard('存储', [
        diagLine('MySQL', diagStatus(mysql.available)),
        diagLine('Redis', diagStatus(redis.available)),
        diagLine('Auth', diagStatus(auth.login_ready !== false))
      ].join('')) +
      diagCard('缓存', [
        diagLine('内存快照', htmlSafe(String(cache.memory_items || 0))),
        diagLine('市场锁', htmlSafe(String(cache.market_lock_count || 0))),
        diagLine('TTL', htmlSafe(String(cache.ttl_seconds || 0)) + 's')
      ].join('')) +
      diagCard('分析器', [
        diagLine('文件', diagStatus(analyzer.path_exists)),
        diagLine('并发', htmlSafe(String(analyzer.max_parallel_runs || '-'))),
        diagLine('超时', htmlSafe(String(analyzer.run_timeout_seconds || '-')) + 's')
      ].join('')) +
      diagCard('实时流', [
        diagLine('Hub', htmlSafe(String(realtime.hub_count || 0))),
        diagLine('SSE', htmlSafe(String(realtime.sse_max_seconds || 0)) + 's'),
        diagLine('复用率', htmlSafe(String(sharingReused) + '/' + String(sharingTotal) + ' · ' + sharingRate.toFixed(1) + '%')),
        diagLine('模式', htmlSafe('exact ' + Number(sharingCounts.exact || 0) + ' / new ' + Number(sharingCounts.new || 0) + ' / superset ' + Number(sharingCounts.superset || 0))),
        hubHtml
      ].join('')) +
      diagCard('实盘复盘', [
        diagLine('记录', htmlSafe(String(review.sampled_records || 0))),
        diagLine('待评估', htmlSafe(String(review.pending || 0))),
        diagLine('到期堆积', htmlSafe(String(review.due_pending || 0))),
        diagLine('校准', htmlSafe(review.calibration_status || '-')),
        diagLine('成本', htmlSafe(Number(review.estimated_roundtrip_cost_pct || 0).toFixed(2) + '% · fee ' + Number(review.taker_fee_bps || 0) + 'bps / slip ' + Number(review.slippage_bps || 0) + 'bps')),
        diagLine('评估器', htmlSafe(evaluator.running ? 'running' : 'idle')),
        diagLine('触发/启动', htmlSafe(String(evaluator.trigger_count || 0) + '/' + String(evaluator.thread_started_count || 0))),
        diagLine('跳过', htmlSafe('recent ' + Number(evaluator.skipped_recent_count || 0) + ' / running ' + Number(evaluator.skipped_running_count || 0)))
      ].join('')) +
      diagCard('Auth 细节', [
        diagLine('已有用户', diagStatus(auth.has_users)),
        diagLine('可初始化', diagStatus(auth.can_create_first_admin || auth.has_users)),
        diagLine('问题', htmlSafe(auth.issue || '-'))
      ].join('')) +
    '</div>';
  }
  function loadDiagnostics() {
    var body = document.getElementById('diagnostics-body');
    if (body) body.innerHTML = '<div class="diag-card"><div class="diag-line"><span>读取中</span><b>...</b></div></div>';
    setDiagnosticsMessage('', '');
    fetch('api/diagnostics', { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (payload) {
        renderDiagnosticsPayload(payload);
      })
      .catch(function (err) {
        setDiagnosticsMessage('诊断读取失败：' + err.message, 'err');
      });
  }
  function openDiagnosticsModal() {
    var modal = document.getElementById('diagnostics-modal');
    if (!modal) return;
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    loadDiagnostics();
  }
  function bindDiagnostics() {
    var btn = document.getElementById('diagnostics-btn');
    var raw = document.getElementById('diagnostics-raw');
    var refresh = document.getElementById('diagnostics-refresh');
    var close = document.getElementById('diagnostics-close');
    var modal = document.getElementById('diagnostics-modal');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', openDiagnosticsModal);
    }
    if (raw && !raw._bound) {
      raw._bound = true;
      raw.addEventListener('click', function () {
        var win = window.open('api/diagnostics', '_blank', 'noopener');
        if (win) win.opener = null;
      });
    }
    if (refresh && !refresh._bound) {
      refresh._bound = true;
      refresh.addEventListener('click', loadDiagnostics);
    }
    if (close && !close._bound) {
      close._bound = true;
      close.addEventListener('click', closeDiagnosticsModal);
    }
    if (modal && !modal._bound) {
      modal._bound = true;
      modal.addEventListener('click', function (e) {
        if (e.target === modal) closeDiagnosticsModal();
      });
    }
  }
  function setPasswordMessage(text, cls) {
    var el = document.getElementById('password-message');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'password-message' + (cls ? ' ' + cls : '');
  }
  function closePasswordModal() {
    var modal = document.getElementById('password-modal');
    if (modal) {
      modal.classList.remove('show');
      modal.setAttribute('aria-hidden', 'true');
    }
    setPasswordMessage('', '');
  }
  function openPasswordModal() {
    var modal = document.getElementById('password-modal');
    if (!modal) return;
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    setPasswordMessage('', '');
    ['password-current', 'password-new', 'password-confirm'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var first = document.getElementById('password-current');
    if (first) first.focus();
  }
  function submitPasswordChange() {
    var current = document.getElementById('password-current');
    var next = document.getElementById('password-new');
    var confirm = document.getElementById('password-confirm');
    var submit = document.getElementById('password-submit');
    var currentValue = current ? current.value : '';
    var nextValue = next ? next.value : '';
    var confirmValue = confirm ? confirm.value : '';
    if (!currentValue || !nextValue) {
      setPasswordMessage('请填写当前密码和新密码。', 'err');
      return;
    }
    if (nextValue.length < 8) {
      setPasswordMessage('新密码至少 8 位。', 'err');
      return;
    }
    if (nextValue !== confirmValue) {
      setPasswordMessage('两次新密码不一致。', 'err');
      return;
    }
    if (nextValue === currentValue) {
      setPasswordMessage('新密码不能和当前密码一样。', 'err');
      return;
    }
    if (submit) submit.disabled = true;
    setPasswordMessage('正在保存...', '');
    fetch('api/auth/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: currentValue,
        new_password: nextValue,
        confirm_password: confirmValue
      }),
      cache: 'no-store'
    })
      .then(function (res) {
        return res.json().then(function (body) { return { ok: res.ok, body: body }; });
      })
      .then(function (result) {
        if (!result.ok || !result.body || !result.body.changed) {
          throw new Error((result.body && result.body.error) || '修改失败');
        }
        setPasswordMessage('密码已修改，其他设备的旧登录会失效。', 'ok');
        setTimeout(closePasswordModal, 900);
      })
      .catch(function (err) {
        setPasswordMessage(err.message || '修改失败', 'err');
      })
      .then(function () {
        if (submit) submit.disabled = false;
      });
  }
  function bindPasswordChange() {
    var btn = document.getElementById('change-password-btn');
    var form = document.getElementById('password-form');
    var close = document.getElementById('password-close');
    var cancel = document.getElementById('password-cancel');
    var modal = document.getElementById('password-modal');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', openPasswordModal);
    }
    if (form && !form._bound) {
      form._bound = true;
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        submitPasswordChange();
      });
    }
    [close, cancel].forEach(function (item) {
      if (item && !item._bound) {
        item._bound = true;
        item.addEventListener('click', closePasswordModal);
      }
    });
    if (modal && !modal._bound) {
      modal._bound = true;
      modal.addEventListener('click', function (e) {
        if (e.target === modal) closePasswordModal();
      });
    }
  }
  function setRegisterMessage(text, cls) {
    var el = document.getElementById('register-message');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'password-message' + (cls ? ' ' + cls : '');
  }
  function closeRegisterModal() {
    var modal = document.getElementById('register-modal');
    if (modal) {
      modal.classList.remove('show');
      modal.setAttribute('aria-hidden', 'true');
    }
    setRegisterMessage('', '');
  }
  function openRegisterModal() {
    var modal = document.getElementById('register-modal');
    if (!modal) return;
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    setRegisterMessage('', '');
    ['register-username', 'register-password', 'register-confirm'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var role = document.getElementById('register-role');
    if (role) role.value = 'user';
    var first = document.getElementById('register-username');
    if (first) first.focus();
  }
  function validRegisterUsername(name) {
    return /^[A-Za-z0-9._@-]{3,64}$/.test(String(name || '').trim());
  }
  function submitCreateUser() {
    var usernameEl = document.getElementById('register-username');
    var passwordEl = document.getElementById('register-password');
    var confirmEl = document.getElementById('register-confirm');
    var roleEl = document.getElementById('register-role');
    var submit = document.getElementById('register-submit');
    var username = usernameEl ? usernameEl.value.trim() : '';
    var password = passwordEl ? passwordEl.value : '';
    var confirm = confirmEl ? confirmEl.value : '';
    var role = roleEl ? roleEl.value : 'user';
    if (!validRegisterUsername(username)) {
      setRegisterMessage('账号 3-64 位，只能用英文、数字、点、横线、下划线或 @。', 'err');
      return;
    }
    if (password.length < 8) {
      setRegisterMessage('密码至少 8 位。', 'err');
      return;
    }
    if (password !== confirm) {
      setRegisterMessage('两次密码不一致。', 'err');
      return;
    }
    if (submit) submit.disabled = true;
    setRegisterMessage('正在创建账号...', '');
    fetch('api/auth/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, password: password, role: role }),
      cache: 'no-store'
    })
      .then(function (res) {
        return res.json().then(function (body) { return { ok: res.ok, body: body }; });
      })
      .then(function (result) {
        if (!result.ok || !result.body || !result.body.created) {
          throw new Error((result.body && result.body.error) || '创建失败');
        }
        setRegisterMessage('账号 ' + result.body.user.username + ' 已创建。', 'ok');
        if (passwordEl) passwordEl.value = '';
        if (confirmEl) confirmEl.value = '';
      })
      .catch(function (err) {
        setRegisterMessage(err.message || '创建失败', 'err');
      })
      .then(function () {
        if (submit) submit.disabled = false;
      });
  }
  function updateAdminControls() {
    var isAdmin = CURRENT_USER && CURRENT_USER.role === 'admin';
    var items = document.querySelectorAll('[data-admin-only]');
    for (var i = 0; i < items.length; i++) {
      if (isAdmin) items[i].classList.remove('admin-only');
      else if (!items[i].classList.contains('admin-only')) items[i].classList.add('admin-only');
    }
  }
  function loadCurrentUser(done) {
    fetch('api/auth/me', { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        CURRENT_USER = payload && payload.authenticated ? payload.user : null;
        updateAdminControls();
      })
      .catch(function () {
        CURRENT_USER = null;
        updateAdminControls();
      })
      .then(function () { if (done) done(); });
  }
  function bindCreateUser() {
    var btn = document.getElementById('create-user-btn');
    var form = document.getElementById('register-form');
    var close = document.getElementById('register-close');
    var cancel = document.getElementById('register-cancel');
    var modal = document.getElementById('register-modal');
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener('click', openRegisterModal);
    }
    if (form && !form._bound) {
      form._bound = true;
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        submitCreateUser();
      });
    }
    [close, cancel].forEach(function (item) {
      if (item && !item._bound) {
        item._bound = true;
        item.addEventListener('click', closeRegisterModal);
      }
    });
    if (modal && !modal._bound) {
      modal._bound = true;
      modal.addEventListener('click', function (e) {
        if (e.target === modal) closeRegisterModal();
      });
    }
  }
  function renderTradingViewKline() {
    var host = document.getElementById('tv-kline');
    if (!host) return;
    bindTradingViewToolbar();
    var r = cur();
    if (!r || !r.symbol) {
      renderTradingViewEmpty('No active symbol for TradingView K-Line.');
      return;
    }
    var tvSymbol = tradingViewSymbol(r.symbol);
    if (!tvSymbol) {
      renderTradingViewEmpty('TradingView symbol is unavailable.');
      return;
    }
    var sub = document.getElementById('tv-kline-sub');
    if (sub) sub.textContent = r.symbol + ' / ' + tvSymbol + ' / ' + tvIntervalLabel(tvKlineInterval);
    var openTv = document.getElementById('tv-open-tv');
    if (openTv) openTv.href = tradingViewUrl(tvSymbol);
    var openBinance = document.getElementById('tv-open-binance');
    if (openBinance) openBinance.href = binanceFuturesUrl(r.symbol);
    var key = tvSymbol + '|' + tvKlineInterval;
    if (key === tvKlineKey && host.querySelector('.tradingview-widget-container')) return;
    tvKlineKey = key;
    host.innerHTML = '<div class="tv-empty">Loading TradingView K-Line for ' + r.symbol + '...</div>';
    var container = document.createElement('div');
    container.className = 'tradingview-widget-container';
    var widget = document.createElement('div');
    widget.className = 'tradingview-widget-container__widget';
    container.appendChild(widget);
    var script = document.createElement('script');
    script.type = 'text/javascript';
    script.async = true;
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: tvKlineInterval,
      timezone: 'Asia/Shanghai',
      theme: 'dark',
      style: '1',
      locale: 'zh_CN',
      backgroundColor: '#050a14',
      gridColor: 'rgba(26,44,74,0.6)',
      withdateranges: true,
      hide_side_toolbar: false,
      allow_symbol_change: false,
      save_image: false,
      calendar: false,
      support_host: 'https://www.tradingview.com',
      studies: ['STD;Volume']
    });
    script.onerror = function () {
      if (tvKlineKey === key) tvKlineKey = '';
      host.innerHTML = '<div class="tv-empty">TradingView K-Line failed to load. Use the links above to open ' + r.symbol + ' directly.</div>';
    };
    host.innerHTML = '';
    host.appendChild(container);
    container.appendChild(script);
  }

  /* ---------- clock ---------- */
  function tick() {
    var d = new Date();
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    // Beijing time = UTC+8
    var bj = new Date(d.getTime() + d.getTimezoneOffset() * 60000 + 8 * 3600000);
    var t = p(bj.getHours()) + ':' + p(bj.getMinutes()) + ':' + p(bj.getSeconds());
    var dt = bj.getFullYear() + '-' + p(bj.getMonth() + 1) + '-' + p(bj.getDate());
    var ce = document.getElementById('clock'); if (ce) ce.textContent = t;
    var de = document.getElementById('clockd'); if (de) de.textContent = dt + ' · 北京时间 UTC+8';
    setGen();
    var active = cur();
    if (active && active._price_snapshot_ms && Date.now() - active._price_snapshot_ms > 20000) {
      updateLiveBadge(false);
    }
  }
  function setGen() {
    var r = cur();
    var strategyEl = document.getElementById('strategy-age');
    var priceEl = document.getElementById('price-age');
    var countdownEl = document.getElementById('refresh-countdown');
    var oldEl = document.getElementById('genat');
    var strategyMs = r && r._strategy_snapshot_ms ? r._strategy_snapshot_ms : parseSnapshotMs(GEN);
    var priceMs = r && r._price_snapshot_ms ? r._price_snapshot_ms : 0;
    if (strategyEl) {
      strategyEl.textContent = strategyMs ? relativeSnapshot(strategyMs) : '—';
      strategyEl.title = (r && r._strategy_snapshot_at) || GEN || '';
    }
    if (priceEl) {
      priceEl.textContent = priceMs ? '' : '等待实时价';
      priceEl.title = priceMs ? 'WebSocket 实时价格' : '';
    }
    if (countdownEl) {
      countdownEl.textContent = formatCountdown(nextStrategyRefreshAt);
    }
    if (oldEl) {
      oldEl.textContent = strategyMs ? relativeSnapshot(strategyMs) : '—';
      oldEl.title = (r && r._strategy_snapshot_at) || GEN || '';
    }
  }
  function parseSnapshotMs(text) {
    if (!text) return 0;
    var raw = String(text).trim().replace(/\//g, '-');
    var m = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2}):(\d{1,2})$/);
    if (m) {
      return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 8, +m[5], +m[6]);
    }
    var ms = Date.parse(raw);
    return isNaN(ms) ? 0 : ms;
  }
  function relativeSnapshot(ms) {
    if (!ms) return GEN || '—';
    var diff = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (diff < 5) return '刚刚';
    if (diff < 60) return diff + '秒前';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return Math.floor(diff / 86400) + '天前';
  }
  function realtimePriceText(r) {
    var v = r ? Number(r.last) : NaN;
    return isFinite(v) ? fmtPrice(v) : '--';
  }
  function backtestSummary(bt) {
    if (!bt || !bt.windows || !bt.windows.length) return '暂无回测';
    var w = bt.windows[bt.windows.length - 1];
    if (!w || !w.sample_count) return '样本不足';
    var stop = w.stop_rate != null ? ' / 止损' + Number(w.stop_rate).toFixed(0) + '%' : '';
    var net = w.net_expectancy_pct != null ? ' / 净期望' + Number(w.net_expectancy_pct).toFixed(2) + '%' : '';
    var filtered = w.filtered_out_count ? ' / 过滤' + w.filtered_out_count : '';
    return '5m代理·后续1H ' + Number(w.hit_rate).toFixed(0) + '% / 盈撤比' + Number(w.profit_drawdown_ratio).toFixed(2) + stop + net + filtered + ' / ' + (bt.grade || '-');
  }
  function compactBacktest(bt) {
    if (!bt || !bt.windows || !bt.windows.length) return '暂无';
    var text = bt.windows.map(function (w) {
      if (!w.sample_count) return w.horizon + ': 样本少';
      var sampleWarn = w.sample_count < 20 ? '样本少' : '';
      var stop = w.stop_rate != null ? '/止损' + Number(w.stop_rate).toFixed(0) + '%' : '';
      return w.horizon + ' ' + Number(w.hit_rate).toFixed(0) + '%' + stop + (sampleWarn ? '(' + sampleWarn + ')' : '');
    }).join(' · ');
    var filteredTotal = bt.windows.reduce(function (sum, w) { return sum + (Number(w.filtered_out_count) || 0); }, 0);
    text = '5m代理（不参与开仓分）· ' + text;
    return filteredTotal ? text + ' · 样本经ATR/量能过滤' : text;
  }
  function triggerLabel(t) {
    if (!t) return '等待触发';
    var suffix = t.distance_pct != null && isFinite(Number(t.distance_pct)) ? ' 距' + Number(t.distance_pct).toFixed(2) + '%' : '';
    return (t.label || t.status || '等待触发') + suffix;
  }
  function gateText(v) {
    return v || '正常';
  }
  function formatCountdown(targetMs) {
    if (!targetMs) return '未启动';
    var left = Math.max(0, Math.ceil((targetMs - Date.now()) / 1000));
    if (!left) return strategyRefreshBusy ? '刷新中' : '即将刷新';
    var m = Math.floor(left / 60);
    var s = left % 60;
    return m + '分' + (s < 10 ? '0' : '') + s + '秒';
  }
  function todayKey() {
    var d = new Date();
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }
  function nextLocalMidnightMs() {
    var d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 0, 0, 0, 0).getTime();
  }
  function loadAccountRiskState() {
    try {
      var raw = localStorage.getItem(ACCOUNT_RISK_KEY);
      var state = raw ? JSON.parse(raw) : {};
      if (!state || typeof state !== 'object') state = {};
      return {
        date: state.date || todayKey(),
        dailyLossPct: Number(state.dailyLossPct) || 0,
        consecutiveLosses: Number(state.consecutiveLosses) || 0,
        singleLossPct: Number(state.singleLossPct) || 0,
        fuseUntil: Number(state.fuseUntil) || 0,
        fuseReasons: Array.isArray(state.fuseReasons) ? state.fuseReasons : []
      };
    } catch (e) {
      return { date: todayKey(), dailyLossPct: 0, consecutiveLosses: 0, singleLossPct: 0, fuseUntil: 0, fuseReasons: [] };
    }
  }
  function saveAccountRiskState(state) {
    try { localStorage.setItem(ACCOUNT_RISK_KEY, JSON.stringify(state)); } catch (e) {}
    saveServerPreferences({ account_risk: state || {} });
  }
  function resetAccountRiskState() {
    var state = { date: todayKey(), dailyLossPct: 0, consecutiveLosses: 0, singleLossPct: 0, fuseUntil: 0, fuseReasons: [] };
    saveAccountRiskState(state);
    return state;
  }
  function accountFuseStatus() {
    var state = loadAccountRiskState();
    var now = Date.now();
    if (state.date !== todayKey() && state.fuseUntil <= now) {
      state = resetAccountRiskState();
    }
    var reasons = [];
    if (state.dailyLossPct >= 3) reasons.push('日亏' + state.dailyLossPct.toFixed(2) + '%');
    if (state.consecutiveLosses >= 3) reasons.push('连续亏损' + Math.round(state.consecutiveLosses) + '次');
    if (state.singleLossPct >= 1.5) reasons.push('单笔亏损' + state.singleLossPct.toFixed(2) + '%');
    if (reasons.length && state.fuseUntil <= now) {
      state.fuseUntil = nextLocalMidnightMs();
      state.fuseReasons = reasons;
      state.date = todayKey();
      saveAccountRiskState(state);
    }
    var active = state.fuseUntil > now;
    var activeReasons = reasons.length ? reasons : state.fuseReasons;
    return {
      active: active,
      state: state,
      reasons: activeReasons,
      until: state.fuseUntil,
      text: active ? '账户熔断：' + activeReasons.join(' / ') + '，次日自动恢复' : '账户风控正常'
    };
  }
  function applyMarketPayloadState(payload) {
    MARKET_STATE = {
      stale: !!(payload && payload.stale),
      warning: (payload && payload.warning) || '',
      errorType: (payload && payload.error_type) || ''
    };
  }
  function marketWarningText() {
    if (!MARKET_STATE.stale) return '';
    return MARKET_STATE.warning || '当前使用旧策略快照，数据可能延迟';
  }
  function stampReports(arr, generatedAt) {
    var oldBySymbol = {};
    DATA.forEach(function (old) {
      if (old && old.symbol) oldBySymbol[old.symbol] = old;
    });
    var stamp = generatedAt || GEN || '';
    var ms = parseSnapshotMs(stamp) || Date.now();
    return (arr || []).map(function (r) {
      if (!r || typeof r !== 'object') return r;
      var old = oldBySymbol[r.symbol];
      if (old) {
        ['_price_snapshot_ms', '_price_bid', '_price_ask', '_depth_imbalance', '_bid_depth_top5_usd', '_ask_depth_top5_usd', '_depth_ladder'].forEach(function (key) {
          if (old[key] != null && r[key] == null) r[key] = old[key];
        });
      }
      r._snapshot_at = stamp;
      r._snapshot_ms = ms;
      r._strategy_snapshot_at = stamp;
      r._strategy_snapshot_ms = ms;
      r._analysis_last = Number(r.last) || 0;
      r._analysis_confidence = Number((r.signal_quality && r.signal_quality.execution_score) || r.confidence) || 0;
      r._payload_stale = MARKET_STATE.stale;
      r._payload_warning = MARKET_STATE.warning;
      r._payload_error_type = MARKET_STATE.errorType;
      (r.timeframe_advice || []).forEach(function (a) {
        a._base_long_entry = Number(a.long_entry);
        a._base_short_entry = Number(a.short_entry);
        a._base_stop_hint = Number(a.stop_hint);
      });
      return r;
    });
  }
  function normalizeClientSymbol(raw) {
    var symbol = String(raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (symbol && symbol.indexOf('USDT') < 0) symbol += 'USDT';
    return symbol;
  }
  function normalizeSymbolList(items) {
    var out = [];
    (items || []).forEach(function (item) {
      var sym = normalizeClientSymbol(item && item.symbol ? item.symbol : item);
      if (sym && out.indexOf(sym) < 0) out.push(sym);
    });
    return out;
  }
  function loadSymbolHistory() {
    try {
      var v = localStorage.getItem(SYMBOL_HISTORY_KEY);
      var arr = v ? JSON.parse(v) : [];
      return normalizeSymbolList(Array.isArray(arr) ? arr : []);
    } catch (e) { return []; }
  }
  function saveSymbolHistory(symbols, syncServer) {
    var out = normalizeSymbolList(symbols).slice(0, 48);
    try { localStorage.setItem(SYMBOL_HISTORY_KEY, JSON.stringify(out)); } catch (e) {}
    if (syncServer !== false) saveServerPreferences({ symbol_history: out });
  }
  function rememberSymbols(symbols, syncServer) {
    var merged = loadSymbolHistory();
    normalizeSymbolList(symbols).forEach(function (sym) {
      merged = merged.filter(function (item) { return item !== sym; });
      merged.unshift(sym);
    });
    saveSymbolHistory(merged, syncServer);
  }
  function activeSymbolMap() {
    var active = {};
    DATA.forEach(function (r) {
      var sym = normalizeClientSymbol(r && r.symbol);
      if (sym) active[sym] = true;
    });
    return active;
  }
  function symbolHistoryCandidates() {
    var active = activeSymbolMap();
    return loadSymbolHistory().filter(function (sym) { return !active[sym]; }).slice(0, 18);
  }
  function seedSymbolHistoryFromLocal() {
    var seed = [];
    seed = seed.concat(loadCustomSyms(), loadRemovedSyms());
    loadSignalHistory().forEach(function (item) {
      if (item && item.symbol) seed.push(item.symbol);
    });
    DATA.forEach(function (r) { if (r && r.symbol) seed.push(r.symbol); });
    if (seed.length) rememberSymbols(seed, true);
  }
  function wantedRefreshSymbols() {
    var removed = loadRemovedSyms();
    var out = [];
    function add(sym) {
      var symbol = normalizeClientSymbol(sym);
      if (!symbol || removed.indexOf(symbol) >= 0 || out.indexOf(symbol) >= 0) return;
      out.push(symbol);
    }
    DATA.forEach(function (r) { if (r && r.symbol) add(r.symbol); });
    loadCustomSyms().forEach(add);
    return out;
  }
  function mergeFreshReports(fresh) {
    var removed = loadRemovedSyms();
    var freshSymbols = {};
    var merged = [];
    (fresh || []).forEach(function (r) {
      if (!r || !r.symbol || removed.indexOf(r.symbol) >= 0) return;
      freshSymbols[r.symbol] = true;
      merged.push(r);
    });
    DATA.forEach(function (old) {
      if (!old || !old.symbol || freshSymbols[old.symbol] || removed.indexOf(old.symbol) >= 0) return;
      merged.push(old);
    });
    return merged;
  }
  function directionScoreOf(advice, report) {
    if (advice && advice.direction_score != null) return Number(advice.direction_score) || 0;
    if (report && report.signal_quality && report.signal_quality.direction_score != null) return Number(report.signal_quality.direction_score) || 0;
    return advice && advice.confidence != null ? Number(advice.confidence) || 0 : Number(report && report.confidence) || 0;
  }
  function executionScoreOf(advice, report) {
    if (advice && advice.execution_score != null) return Number(advice.execution_score) || 0;
    if (advice && advice.confidence != null) return Number(advice.confidence) || 0;
    if (report && report.signal_quality && report.signal_quality.execution_score != null) return Number(report.signal_quality.execution_score) || 0;
    return Number(report && report.confidence) || 0;
  }
  function bestSignalAdvice(r) {
    if (!r || !r.timeframe_advice || !r.timeframe_advice.length) return null;
    return r.timeframe_advice.reduce(function (a, b) { return executionScoreOf(a, r) > executionScoreOf(b, r) ? a : b; });
  }
  function horizonMatches(advice, horizon) {
    var name = String((advice && advice.name) || '');
    var h = String(horizon || '');
    return !h || name === h || name.indexOf(h) === 0;
  }
  function selectedSignalAdvice(r) {
    if (!r || !r.timeframe_advice || !r.timeframe_advice.length) return null;
    var h = currentHorizon || '超短线';
    for (var i = 0; i < r.timeframe_advice.length; i++) {
      if (horizonMatches(r.timeframe_advice[i], h)) return r.timeframe_advice[i];
    }
    return bestSignalAdvice(r);
  }
  function isShortBias(b) {
    var s = String(b || '');
    return s.indexOf('观望') < 0 && s.indexOf('偏空') >= 0;
  }
  function isLongBias(b) {
    var s = String(b || '');
    return s.indexOf('观望') < 0 && s.indexOf('偏多') >= 0;
  }
  function adviceSide(advice) {
    var b = String((advice && advice.bias) || '');
    if (isShortBias(b)) return 'short';
    if (isLongBias(b)) return 'long';
    return 'wait';
  }
  function entryLabel(side) {
    if (side === 'short') return '反弹空点';
    if (side === 'long') return '回踩多点';
    return '观察价';
  }
  function adviceValue(advice, key) {
    if (!advice) return 0;
    var baseKey = key === 'long_entry' ? '_base_long_entry' : key === 'short_entry' ? '_base_short_entry' : '_base_stop_hint';
    var base = Number(advice[baseKey] != null ? advice[baseKey] : advice[key]);
    if (!isFinite(base)) return 0;
    return base;
  }
  function realtimeConfidence(r, advice) {
    if (!r) return 0;
    var base = advice
      ? executionScoreOf(advice, r)
      : Number(r._analysis_confidence != null ? r._analysis_confidence : r.confidence || 0);
    var analysisLast = Number(r._analysis_last || r.last || 0);
    var liveLast = Number(r.last || analysisLast);
    if (!analysisLast || !isFinite(base)) return base;
    var movePct = Math.abs((liveLast - analysisLast) / analysisLast) * 100;
    var penalty = movePct >= 3 ? 35 : movePct >= 2 ? 25 : movePct >= 1 ? 15 : movePct >= 0.5 ? 8 : 0;
    return Math.max(0, Math.min(100, Math.round(base - penalty)));
  }
  function signalSnapshot(r) {
    var advice = selectedSignalAdvice(r);
    var side = adviceSide(advice);
    var entryKey = side === 'short' ? 'short_entry' : 'long_entry';
    var confidence = realtimeConfidence(r, advice);
    var directionScore = directionScoreOf(advice, r);
    var entry = advice ? adviceValue(advice, entryKey) : null;
    var sizing = sizingOf(advice, r);
    return {
      advice: advice,
      side: side,
      entryLabel: entryLabel(side),
      entryDistance: entry == null ? '' : entryDistanceText(r, entry),
      entry: entry,
      stop: advice ? adviceValue(advice, 'stop_hint') : null,
      confidence: confidence,
      directionScore: directionScore,
      executionScore: confidence,
      candleState: advice ? advice.candle_state : '',
      riskGate: advice ? advice.risk_gate : '',
      backtest: advice ? advice.backtest : null,
      trigger: advice ? advice.trigger_check : null,
      sizing: sizing,
      position: positionFromSizing(sizing, confidence)
    };
  }
  function maxAtrPct(r) {
    var max = 0;
    if (!r || !r.indicators) return max;
    TFS.forEach(function (tf) {
      var v = r.indicators[tf] && Number(r.indicators[tf].atr14_pct);
      if (isFinite(v)) max = Math.max(max, v);
    });
    return max;
  }
  function selectedAtrPct(r, advice) {
    if (!r || !r.indicators) return 0;
    var name = String((advice && advice.name) || currentHorizon || '');
    var tf = '5m';
    if (name.indexOf('短线') === 0) tf = '1h';
    else if (name.indexOf('波段') === 0) tf = '4h';
    else if (name.indexOf('一周') === 0) tf = '8h';
    var item = r.indicators[tf];
    var v = item && Number(item.atr14_pct);
    return isFinite(v) ? v : maxAtrPct(r);
  }
  function signalDistanceModel(r, snap) {
    var atrPct = selectedAtrPct(r, snap && snap.advice);
    if (!isFinite(atrPct) || atrPct <= 0) atrPct = maxAtrPct(r);
    if (!isFinite(atrPct) || atrPct <= 0) atrPct = 1;
    return {
      atrPct: atrPct,
      stopDangerPct: Math.max(0.18, Math.min(1.2, atrPct * 0.25)),
      entryHotPct: Math.max(0.12, Math.min(1.2, atrPct * 0.30)),
      entryWatchPct: Math.max(0.35, Math.min(2.5, atrPct * 1.00)),
      chasePct: Math.max(0.70, Math.min(2.2, atrPct * 0.90)),
      stalePct: Math.max(0.90, Math.min(4.0, atrPct * 1.50))
    };
  }
  function atrMultipleText(pct, model) {
    if (pct == null || !model || !model.atrPct) return '';
    return (pct / model.atrPct).toFixed(1) + 'xATR';
  }
  function riskLevel(r) {
    var risks = r && r.risks ? r.risks : [];
    var advice = selectedSignalAdvice(r);
    var gate = (advice && advice.risk_gate) || (r && r.signal_quality && r.signal_quality.risk_gate) || '';
    var fuse = accountFuseStatus();
    if (fuse.active) {
      return { key: 'block', label: '账户熔断', text: fuse.text };
    }
    if (gate === '禁止开仓') {
      return { key: 'block', label: '禁止开仓', text: '后端风控阀门已触发：极端波动或信号质量不足，先不要开新仓。' };
    }
    if (gate === '禁止半仓') {
      return { key: 'high', label: '高风险', text: '后端风控阀门已触发：禁止半仓，最多轻仓观察。' };
    }
    var dangerCount = risks.filter(function (x) { return /过热|很高|很大|容易快速|过高|极端|追|扫掉|穿越/.test(x); }).length;
    var atr = selectedAtrPct(r, advice);
    var conf = realtimeConfidence(r, advice);
    if (advice && String(advice.bias || '').indexOf('观望') >= 0 && conf < 60) {
      return { key: 'high', label: '高风险', text: '当前周期方向不够干净，先等触发位或换更短确认。' };
    }
    if (dangerCount >= 4 || atr >= 18) return { key: 'block', label: '禁止开仓', text: '前端兜底风控：波动或异常提示极端，先别开新仓。' };
    if (conf < 40 || dangerCount >= 3 || atr >= 12) return { key: 'high', label: '等待确认', text: '开仓执行分过低或波动偏大，先等入场触发和完整 K 线确认。' };
    if (conf < 55 || dangerCount >= 2 || risks.length >= 4 || atr >= 8) return { key: 'high', label: '高风险', text: '只能轻仓或等待更干净的位置。' };
    var exposure = correlationExposureForSymbol(r && r.symbol);
    if (exposure) return { key: 'mid', label: '相关性风险', text: exposure.text };
    if (conf < 70 || dangerCount >= 1 || risks.length >= 2 || atr >= 4) return { key: 'mid', label: '中风险', text: '允许观察，仓位需要控制。' };
    return { key: 'low', label: '低风险', text: '风险相对可控，但仍需止损。' };
  }
  function isRealtimePrejudgeSnap(snap) {
    if (!snap) return false;
    if (snap.candleState === '实时K线' || snap.candleState === '实时预判') return true;
    var t = snap.trigger || {};
    return String(t.label || '').indexOf('实时K线') >= 0 || (t.reasons || []).some(function (x) { return String(x).indexOf('仍在形成') >= 0 || String(x).indexOf('实时K线') >= 0; });
  }
  function riskAdjustedPosition(pos, risk, snap) {
    if (!pos || !risk) return pos;
    if (accountFuseStatus().active) return { stars: '', text: '0%', sizePct: 0, color: C.bear, note: '账户熔断禁止开仓' };
    if (isRealtimePrejudgeSnap(snap) && snap.trigger && /confirmed|watch/.test(String(snap.trigger.status || ''))) {
      return { stars: '', text: '0%', sizePct: 0, color: C.warn, note: '等待完整 K 线确认' };
    }
    if (risk.key === 'block') return { stars: '', text: '0%', sizePct: 0, color: C.bear, note: risk.text };
    if (risk.key === 'high' && Number(pos.sizePct || 0) > 10) return { stars: '', text: '10%', sizePct: 10, color: C.warn, note: '高风险前端封顶' };
    return pos;
  }
  function isAltSymbol(sym) {
    return !/^BTCUSDT$|^ETHUSDT$/.test(String(sym || '').toUpperCase());
  }
  function correlationExposureForSymbol(symbol) {
    if (!symbol || accountFuseStatus().active) return null;
    var current = null;
    var groups = { long: [], short: [] };
    DATA.forEach(function (r) {
      var advice = selectedSignalAdvice(r);
      var side = adviceSide(advice);
      if (side !== 'long' && side !== 'short') return;
      if (!isAltSymbol(r.symbol)) return;
      var gate = (advice && advice.risk_gate) || (r.signal_quality && r.signal_quality.risk_gate) || '';
      if (gate === '禁止开仓') return;
      var sizing = sizingOf(advice, r);
      var sizePct = sizing ? Number(sizing.suggested_size_pct) || 0 : 0;
      var exec = executionScoreOf(advice, r);
      if (sizePct <= 0 && exec < 55) return;
      var item = { symbol: r.symbol, side: side, sizePct: sizePct, exec: exec };
      groups[side].push(item);
      if (r.symbol === symbol) current = item;
    });
    if (!current) return null;
    var peers = groups[current.side] || [];
    if (peers.length < 2) return null;
    var names = peers.map(function (x) { return x.symbol.replace('USDT', ''); }).join('/');
    var total = peers.reduce(function (sum, x) { return sum + (Number(x.sizePct) || 0); }, 0);
    return {
      side: current.side,
      symbols: peers.map(function (x) { return x.symbol; }),
      totalSizePct: total,
      text: '同向山寨敞口：' + names + ' 同时偏' + (current.side === 'long' ? '多' : '空') + '，总计划仓位约' + fmtSizePct(total) + '，按一组风险看。'
    };
  }
  function distancePct(a, b) {
    if (!a || !b) return null;
    return Math.abs((a - b) / b) * 100;
  }
  function signedDistancePct(target, price) {
    if (!target || !price) return null;
    return ((target - price) / price) * 100;
  }
  function entryDistanceText(r, entry) {
    var d = signedDistancePct(entry, Number(r && r.last));
    if (d == null || !isFinite(d)) return '';
    var model = signalDistanceModel(r, { advice: selectedSignalAdvice(r) });
    return '距现价 ' + (d >= 0 ? '+' : '') + d.toFixed(2) + '% / ' + atrMultipleText(Math.abs(d), model);
  }
  function signalAlert(r, snap) {
    if (!r) return { cls: '', text: '' };
    var fuse = accountFuseStatus();
    if (fuse.active) {
      return { cls: 'danger', text: fuse.text + '。今日不要再开新仓。' };
    }
    if (!snap || snap.entry == null || snap.stop == null) return { cls: '', text: '' };
    var price = Number(r.last);
    var entryDist = distancePct(price, snap.entry);
    var entrySigned = signedDistancePct(snap.entry, price);
    var stopDist = distancePct(price, snap.stop);
    var model = signalDistanceModel(r, snap);
    var driftDist = distancePct(price, Number(r._analysis_last || 0));
    var entryAtrText = entryDist == null ? '' : '，约 ' + atrMultipleText(entryDist, model);
    var state = getPositionState(r.symbol);
    if (stopDist != null && stopDist <= model.stopDangerPct) {
      return { cls: 'danger', text: '接近风控止损，距离约 ' + stopDist.toFixed(2) + '%（' + atrMultipleText(stopDist, model) + '），优先处理风险。' };
    }
    if (state === '多单' && snap.side === 'short') {
      return { cls: 'danger', text: '你标记为多单，但当前信号偏空，优先看止损或减仓。' };
    }
    if (state === '空单' && snap.side === 'long') {
      return { cls: 'danger', text: '你标记为空单，但当前信号偏多，优先看止损或减仓。' };
    }
    if (isRealtimePrejudgeSnap(snap) && snap.trigger && /confirmed|watch/.test(String(snap.trigger.status || ''))) {
      return { cls: 'warn', text: '当前周期 K 线仍在形成；等 1m/对应周期形成完成后再考虑入场。' };
    }
    if (driftDist != null && driftDist >= model.stalePct) {
      return { cls: 'warn', text: '实时价已偏离策略快照 ' + driftDist.toFixed(2) + '%（' + atrMultipleText(driftDist, model) + '），当前信号可能过期，等下一次策略刷新更稳。' };
    }
    if (snap.trigger && snap.trigger.status === 'blocked') {
      return { cls: 'warn', text: '本次入场触发失败：' + ((snap.trigger.reasons || []).join('；') || triggerLabel(snap.trigger)) + '。等下一次结构确认。' };
    }
    if (snap.trigger && snap.trigger.status === 'confirmed') {
      return { cls: 'watch', text: '入场触发已确认：量能、价差和1m结构通过，仍需按仓位和止损执行。' };
    }
    if (snap.trigger && snap.trigger.status === 'watch') {
      return { cls: 'warn', text: '价格到位但还要等确认：' + (snap.trigger.reasons || []).join('；') };
    }
    if (entryDist != null && entryDist <= model.entryHotPct) {
      return { cls: 'watch', text: '已接近' + snap.entryLabel + '，距离约 ' + entryDist.toFixed(2) + '%' + entryAtrText + '，等 1m 触发确认再动手。' };
    }
    if (entryDist != null && entryDist <= model.entryWatchPct) {
      return { cls: 'warn', text: '价格在' + snap.entryLabel + '附近，距离约 ' + entryDist.toFixed(2) + '%' + entryAtrText + '，别追，等触发。' };
    }
    if (snap.side === 'short' && entrySigned != null && entrySigned > model.chasePct) {
      return { cls: 'warn', text: '现价低于反弹空点 ' + entrySigned.toFixed(2) + '%（' + atrMultipleText(entrySigned, model) + '），这里不是追空位置，等反弹触发或看更短周期。' };
    }
    if (snap.side === 'long' && entrySigned != null && Math.abs(entrySigned) > model.chasePct) {
      return { cls: 'warn', text: '现价高于回踩多点 ' + Math.abs(entrySigned).toFixed(2) + '%（' + atrMultipleText(Math.abs(entrySigned), model) + '），这里不是追多位置，等回踩确认。' };
    }
    return { cls: '', text: '' };
  }
  function signalTopline(risk, bestAdvice, snap, state) {
    var fuse = accountFuseStatus();
    if (fuse.active) {
      return { cls: 'danger', text: fuse.text };
    }
    var staleText = marketWarningText();
    if (staleText) {
      return { cls: 'warn', text: '数据可能延迟：' + staleText };
    }
    if (state === '多单' && snap && snap.side === 'short') {
      return { cls: 'danger', text: '持仓冲突：你当前标记为多单，但系统偏空，先处理已有仓位风险。' };
    }
    if (state === '空单' && snap && snap.side === 'long') {
      return { cls: 'danger', text: '持仓冲突：你当前标记为空单，但系统偏多，先处理已有仓位风险。' };
    }
    if (risk && risk.key === 'block') {
      return { cls: 'danger', text: '后端风控已触发：禁止新开仓。' };
    }
    if (bestAdvice && bestAdvice.risk_gate === '禁止半仓') {
      return { cls: 'warn', text: '后端风控已触发：禁止半仓，最多轻仓观察。' };
    }
    var exposure = correlationExposureForSymbol(cur() && cur().symbol);
    if (exposure) {
      return { cls: 'warn', text: exposure.text };
    }
    if (isRealtimePrejudgeSnap(snap)) {
      return { cls: 'warn', text: '当前信号来自仍在形成的 K 线，仅作实时判断，等待完整 K 线确认。' };
    }
    return null;
  }
  function openingGuardDecision(r) {
    if (!r) {
      return {
        key: 'wait',
        label: '等待数据',
        action: '先别开仓',
        reasons: [{ type: 'warn', text: '暂无当前币种策略数据。' }],
        metrics: []
      };
    }
    var fuse = accountFuseStatus();
    var snap = signalSnapshot(r);
    var risk = riskLevel(r);
    var trigger = snap && snap.trigger ? snap.trigger : {};
    var reasons = [];
    var hardBlocks = [];
    var waits = [];
    function add(type, text) {
      var item = { type: type || '', text: text };
      reasons.push(item);
      if (type === 'block') hardBlocks.push(item);
      if (type === 'warn') waits.push(item);
    }

    if (fuse.active) {
      add('block', fuse.text + '。今日不要再开新仓。');
    }

    var staleText = marketWarningText();
    if (staleText) add('warn', '策略数据可能延迟：' + staleText);

    var priceAge = r._price_snapshot_ms ? Math.max(0, Math.floor((Date.now() - r._price_snapshot_ms) / 1000)) : null;
    if (priceAge == null) add('warn', '实时价还没连上，不能用旧价格做触发判断。');
    else if (priceAge > 20) add('warn', '实时价 ' + priceAge + ' 秒未更新，先等 WebSocket 恢复。');

    if (!snap || !snap.advice) {
      add('warn', '没有可执行周期建议，只能观察。');
    } else {
      if (!fuse.active && risk.key === 'block') add('block', risk.text || '当前风险等级为禁止开仓。');
      else if (!fuse.active && risk.key === 'high') add('warn', risk.text || '当前属于高风险，只能观察或轻仓等待。');

      if (snap.side !== 'long' && snap.side !== 'short') {
        add('warn', '方向不是明确多/空，当前不适合开新仓。');
      }
      if (!isFinite(Number(snap.entry)) || Number(snap.entry) <= 0) {
        add('block', '建议入场价无效，禁止开仓。');
      }
      if (!isFinite(Number(snap.stop)) || Number(snap.stop) <= 0) {
        add('block', '风控止损无效，禁止开仓。');
      }
      var guardModel = signalDistanceModel(r, snap);
      var guardDrift = distancePct(Number(r.last), Number(r._analysis_last || 0));
      if (guardDrift != null && guardDrift >= guardModel.stalePct) {
        add('warn', '实时价已偏离策略快照 ' + guardDrift.toFixed(2) + '%（' + atrMultipleText(guardDrift, guardModel) + '），等待下一次策略刷新更稳。');
      }
      if (isRealtimePrejudgeSnap(snap)) {
        add('warn', '当前周期 K 线仍在形成，等完整 K 线确认更稳。');
      }
      if (trigger.status === 'blocked') {
        add('warn', '本次入场触发失败：' + ((trigger.reasons || []).join('；') || triggerLabel(trigger)) + '。等下一次结构确认。');
      } else if (trigger.status === 'watch') {
        add('warn', '价格接近但还没确认：' + ((trigger.reasons || []).join('；') || triggerLabel(trigger)));
      } else if (trigger.status !== 'confirmed') {
        add('warn', '还没有 1m 触发确认，不能把入场价当下单指令。');
      }
      if (Number(snap.executionScore || 0) < 55) {
        add('warn', '开仓执行分低于 55，信号质量不够。');
      }
      var posPct = snap.position ? Number(snap.position.sizePct || 0) : 0;
      if (posPct <= 0) {
        add('warn', '风险预算仓位为 0%，当前不建议开仓。');
      }
    }

    var key = hardBlocks.length ? 'block' : (waits.length ? 'wait' : 'allow');
    var label = key === 'allow' ? '允许观察开仓' : key === 'block' ? '禁止开仓' : '等待确认';
    var action = key === 'allow'
      ? '触发与风控通过，可按风险预算执行'
      : key === 'block'
        ? '禁止新开仓，先处理风险'
        : '先别动，等触发确认或完整 K 线确认';
    if (!reasons.length) {
      reasons.push({ type: '', text: '风控、触发、止损和实时价当前未发现硬性阻断。' });
    }
    var metrics = [];
    if (snap && snap.advice) {
      metrics.push(['风险等级', risk.label || '-']);
      metrics.push(['开仓分', Math.round(Number(snap.executionScore || 0)) + '/100']);
      metrics.push(['K线', snap.candleState || '-']);
      metrics.push(['触发', triggerLabel(trigger)]);
      metrics.push(['建议仓位', snap.position ? snap.position.text : '0%']);
      metrics.push(['实时价', realtimePriceText(r)]);
    }
    return { key: key, label: label, action: action, reasons: reasons, metrics: metrics };
  }
  function loadPositionStates() {
    try { var v = localStorage.getItem(POSITION_STATE_KEY); return v ? JSON.parse(v) : {}; } catch (e) { return {}; }
  }
  function savePositionStates(map) {
    try { localStorage.setItem(POSITION_STATE_KEY, JSON.stringify(map || {})); } catch (e) {}
    saveServerPreferences({ position_state: map || {} });
  }
  function getPositionState(sym) {
    var map = loadPositionStates();
    return map[sym] || '空仓';
  }
  function setPositionState(sym, state) {
    var map = loadPositionStates();
    map[sym] = state;
    savePositionStates(map);
  }
  function bindPositionStateButtons(host) {
    var buttons = host.querySelectorAll('.pos-btn');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function () {
        var box = this.closest('.pos-state');
        var sym = box ? box.getAttribute('data-symbol') : '';
        var state = this.getAttribute('data-state') || '空仓';
        if (!sym) return;
        setPositionState(sym, state);
        for (var j = 0; j < buttons.length; j++) buttons[j].classList.remove('active');
        this.classList.add('active');
        updateRealtimeSignal();
      });
    }
  }
  function bindSignalAlertToggle(host) {
    var btn = host.querySelector('.js-signal-alert-toggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      signalAlertPref.enabled = !signalAlertPref.enabled;
      saveSignalAlertPref();
      signalAlertState.lastKey = '';
      signalAlertState.lastAt = 0;
      updateSignalAlertButton();
      if (signalAlertPref.enabled) playSignalBeep('watch');
    });
    updateSignalAlertButton();
  }
  function loadSignalHistory() {
    try {
      var v = localStorage.getItem(SIGNAL_HISTORY_KEY);
      var arr = v ? JSON.parse(v) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  function saveSignalHistory(arr) {
    try { localStorage.setItem(SIGNAL_HISTORY_KEY, JSON.stringify((arr || []).slice(0, 80))); } catch (e) {}
    saveServerPreferences({ signal_history: (arr || []).slice(0, 80) });
  }
  function shortTime(ms) {
    var d = new Date(ms || Date.now());
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function signalSignature(r) {
    var snap = signalSnapshot(r);
    var risk = riskLevel(r);
    var pos = riskAdjustedPosition(snap.position, risk);
    return [
      r.symbol,
      r.bias,
      snap.executionScore,
      snap.directionScore,
      snap.advice ? snap.advice.name : '',
      snap.entryLabel || '',
      snap.entry == null ? '' : fmtPrice(snap.entry),
      snap.stop == null ? '' : fmtPrice(snap.stop),
      pos ? pos.text : '',
      risk.label
    ].join('|');
  }
  function recordSignalHistory(reason) {
    var history = loadSignalHistory();
    DATA.forEach(function (r) {
      if (!r || !r.symbol) return;
      var sig = signalSignature(r);
      var last = history.find(function (x) { return x.symbol === r.symbol; });
      if (last && last.signature === sig) return;
      var snap = signalSnapshot(r);
      var risk = riskLevel(r);
      var pos = riskAdjustedPosition(snap.position, risk);
      history.unshift({
        ts: Date.now(),
        reason: reason || '策略刷新',
        symbol: r.symbol,
        bias: r.bias,
        confidence: snap.executionScore,
        directionScore: snap.directionScore,
        advice: snap.advice ? snap.advice.name : '',
        entryLabel: snap.entryLabel,
        entryDistance: snap.entryDistance,
        entry: snap.entry,
        stop: snap.stop,
        position: pos ? pos.text : '',
        risk: risk.label,
        signature: sig
      });
    });
    saveSignalHistory(history);
  }
  function renderSignalHistory() {
    var host = document.getElementById('signal-history');
    if (!host) return;
    var r = cur();
    if (!r) { host.innerHTML = '<div class="history-empty">暂无信号历史</div>'; return; }
    var items = loadSignalHistory().filter(function (x) { return x.symbol === r.symbol; }).slice(0, 30);
    if (!items.length) { host.innerHTML = '<div class="history-empty">暂无信号历史</div>'; return; }
    host.innerHTML = items.map(function (x) {
      var biasCls = x.bias === 'bull' ? 'bull' : x.bias === 'bear' ? 'bear' : 'wait';
      var biasText = x.bias === 'bull' ? '偏多' : x.bias === 'bear' ? '偏空' : '观望';
      return '<div class="history-item">' +
        '<div class="ht"><span>' + shortTime(x.ts) + ' · ' + x.reason + '</span><span>' + (x.advice || '') + '</span></div>' +
        '<div class="hb">' +
          '<span class="hl-bias ' + biasCls + '">' + biasText + '</span>' +
          '<span>开仓<span class="hl-conf">' + x.confidence + '</span></span>' +
          '<span>方向<span class="hl-conf">' + (x.directionScore != null ? x.directionScore : '-') + '</span></span>' +
          '<span class="hl-risk">' + x.risk + '</span>' +
          '<span class="sep">|</span>' +
          '<span>' + (x.entryLabel || '入场') + ' <b>' + fmtPrice(Number(x.entry || 0)) + '</b>' + (x.entryDistance ? ' ' + x.entryDistance : '') + '</span>' +
          '<span>止损 <b>' + fmtPrice(Number(x.stop || 0)) + '</b></span>' +
          '<span class="sep">|</span>' +
          '<span>' + x.position + '</span>' +
        '</div>' +
      '</div>';
    }).join('');
  }
  function reviewSideText(side) {
    return side === 'long' ? '做多' : side === 'short' ? '做空' : '观望';
  }
  function reviewReasonText(reason) {
    var map = {
      ok: '有效',
      entry_too_far: '入场太远',
      not_triggered_adverse_move: '未触发且反向',
      stop_too_tight: '止损过窄',
      same_bar_stop: '同K止损',
      stop_hit_first: '先打止损',
      stop_after_profit: '盈利后回撤止损',
      direction_wrong: '方向错误',
      no_follow_through: '突破无延续',
      no_market_data: '行情不足',
      invalid_signal: '信号无效',
      entry_invalid: '入场无效',
      stop_invalid: '止损无效',
      long_stop_not_below_entry: '多单止损不合法',
      short_stop_not_above_entry: '空单止损不合法'
    };
    return map[reason] || reason || '-';
  }
  function reviewReasonClass(reason) {
    if (reason === 'ok' || reason === 'stop_after_profit') return 'good';
    if (/stop|wrong|invalid|adverse/.test(String(reason || ''))) return 'bad';
    return 'warn';
  }
  function reviewPct(v) {
    var n = Number(v);
    if (!isFinite(n)) return '--';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }
  function reviewHorizonLabel(h) {
    return h === '5m' ? '5分钟' : h === '15m' ? '15分钟' : h === '1h' ? '1小时' : h;
  }
  function reviewSampleQualityByCount(count) {
    var n = Number(count) || 0;
    if (n <= 0) return { cls: 'low', label: '无样本', text: '等待真实信号到期后评估。' };
    if (n < 20) return { cls: 'low', label: '样本不足', text: '只能当案例看，不能当胜率。' };
    if (n < 60) return { cls: 'mid', label: '样本积累中', text: '可以参考，但仍要看失败原因。' };
    return { cls: 'good', label: '样本可参考', text: '样本量较足，可用于校准信心。' };
  }
  function reviewOverallQuality(per) {
    var counts = ['5m', '15m', '1h'].map(function (h) {
      return Number((per[h] && per[h].sample_count) || 0);
    });
    var maxCount = Math.max.apply(Math, counts);
    var zeroCount = counts.filter(function (x) { return x <= 0; }).length;
    var positive = counts.filter(function (x) { return x > 0; }).sort(function (a, b) { return a - b; });
    var minPositive = positive[0] || 0;
    var q = reviewSampleQualityByCount(maxCount);
    if (maxCount > 0 && zeroCount) {
      return { cls: 'low', label: '周期样本不全', text: '有些周期还没有到期样本，不能只看已有周期的命中率。' };
    }
    if (maxCount > 0 && minPositive < 20) {
      q = { cls: 'low', label: '样本不足', text: '部分周期样本还少，先看失败原因，不要只看命中率。' };
    }
    return q;
  }
  function reviewEvaluationText(evaluation) {
    var ev = evaluation || {};
    var status = ev.status || {};
    var result = status.last_result || {};
    var reason = ev.reason || '';
    var text = '';
    if (ev.scheduled) text = '评估器已启动';
    else if (reason === 'recently_triggered') text = '刚触发过，等待冷却';
    else if (reason === 'already_running' || status.running) text = '评估器正在运行';
    else if (reason === 'storage_unavailable') text = '复盘存储不可用';
    else if (reason) text = reason;
    else text = '空闲';
    if (result && result.due != null) {
      text += '；上次到期 ' + Number(result.due || 0) + '，更新 ' + Number(result.updated || 0);
    }
    return ' 评估器：' + text + '。';
  }
  function reviewSegmentTitle(name) {
    if (name === 'risk_gate') return '风控';
    if (name === 'trigger_status') return '触发';
    if (name === 'candle_state') return 'K线';
    if (name === 'anchor_interval') return '锚定';
    if (name === 'trend_regime') return '趋势';
    if (name === 'atr_regime') return 'ATR';
    if (name === 'boll_width_regime') return 'BOLL宽度';
    if (name === 'boll_position_regime') return 'BOLL位置';
    return name || '分桶';
  }
  function renderReviewSegments(segments) {
    if (!segments || typeof segments !== 'object') return '';
    var names = ['trigger_status', 'risk_gate', 'candle_state', 'atr_regime', 'trend_regime', 'boll_width_regime'];
    var html = [];
    names.forEach(function (name) {
      var group = segments[name] || {};
      var keys = Object.keys(group).filter(function (key) {
        return group[key] && Number(group[key].sample_count || 0) > 0;
      }).sort(function (a, b) {
        return Number(group[b].sample_count || 0) - Number(group[a].sample_count || 0);
      }).slice(0, 3);
      keys.forEach(function (key) {
        var s = group[key] || {};
        var sampleCount = Number(s.sample_count || 0);
        var triggeredCount = Number(s.triggered_count || 0);
        var quality = reviewSampleQualityByCount(sampleCount);
        var title = reviewSegmentTitle(name) + ' · ' + (name === 'risk_gate' ? gateText(key) : key);
        html.push('<div class="review-seg ' + quality.cls + '">' +
          '<b>' + htmlSafe(title) + '</b>' +
          '<span>样本 ' + sampleCount + ' / 触发 ' + triggeredCount +
          ' / 命中 ' + Number(s.hit_rate_pct || 0).toFixed(0) + '%' +
          (Number(s.invalid_count || 0) ? ' / 无效 ' + Number(s.invalid_rate_pct || 0).toFixed(0) + '%' : '') +
          ' / 结果 ' + reviewPct(s.avg_outcome_pct || 0) + '</span>' +
        '</div>');
      });
    });
    return html.length ? '<div class="review-segments">' + html.join('') + '</div>' : '';
  }
  function reviewCalibrationReason(reason) {
    var map = {
      invalid_rate_high: '无效信号偏多',
      stop_rate_high: '止损率偏高',
      hit_rate_low: '命中率偏低',
      expectancy_negative: '期望为负',
      historically_supported: '历史表现支持'
    };
    return map[reason] || reason || '-';
  }
  function renderReviewCalibration(calibration) {
    if (!calibration || typeof calibration !== 'object') return '';
    var status = calibration.status || 'insufficient_data';
    var minSample = Number(calibration.min_sample_count || 0);
    var minTriggered = Number(calibration.min_triggered_count || 0);
    var calibrationHorizon = calibration.horizon || '1h';
    var candidates = Array.isArray(calibration.candidates) ? calibration.candidates : [];
    var cls = status === 'needs_review' ? 'warn' : status === 'stable' ? 'good' : 'low';
    var title = status === 'needs_review' ? '校准候选' : status === 'stable' ? '暂无明显弱分桶' : '校准样本不足';
    var note = status === 'insufficient_data'
      ? '分桶至少需要 ' + minSample + ' 个样本且触发 ' + minTriggered + ' 次，当前只展示案例，不建议据此调阈值。'
      : status === 'stable'
        ? '已有足够样本的分桶暂未出现明显弱点，继续收集。'
        : '以下分桶样本已够，适合人工复盘后再决定是否降级或提高信心。';
    var body = candidates.slice(0, 6).map(function (item) {
      var actionCls = item.action === 'downgrade' ? 'bad' : 'good';
      var actionText = item.action === 'downgrade' ? '建议降级观察' : '可支持信心';
      return '<div class="review-cal-item ' + actionCls + '">' +
        '<b>' + htmlSafe(reviewSegmentTitle(item.segment) + ' · ' + item.key) + '</b>' +
        '<span>' + actionText + ' · ' + reviewCalibrationReason(item.reason) +
        ' · 独立样本 ' + Number(item.sample_count || 0) + ' / 独立触发 ' + Number(item.triggered_count || 0) +
        ' / 命中 ' + Number(item.hit_rate_pct || 0).toFixed(0) + '%' +
        ' / 止损 ' + Number(item.stop_rate_pct || 0).toFixed(0) + '%' +
        ' / 结果 ' + reviewPct(item.avg_outcome_pct || 0) + '</span>' +
      '</div>';
    }).join('');
    return '<div class="review-calibration ' + cls + '">' +
      '<div class="review-cal-head"><b>' + title + ' [' + htmlSafe(calibrationHorizon) + ']</b><span>' + note + '</span></div>' +
      (body ? '<div class="review-cal-list">' + body + '</div>' : '') +
    '</div>';
  }
  function latestDoneReview(record) {
    var ev = record && record.evaluation && typeof record.evaluation === 'object' ? record.evaluation : {};
    var order = ['1h', '15m', '5m'];
    for (var i = 0; i < order.length; i++) {
      var item = ev[order[i]];
      if (item && item.status === 'done') {
        item._horizon = order[i];
        return item;
      }
    }
    if (ev.invalid) {
      ev.invalid._horizon = 'invalid';
      return ev.invalid;
    }
    return null;
  }
  function csvCell(value) {
    if (value == null) return '';
    var text = String(value);
    if (/[",\r\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }
  function reviewExportValue(record, horizon, field) {
    var ev = record && record.evaluation && typeof record.evaluation === 'object' ? record.evaluation : {};
    var item = ev[horizon] || {};
    return item[field] == null ? '' : item[field];
  }
  function exportSignalReviewCsv() {
    var r = cur();
    var key = r && r.symbol;
    var payload = SIGNAL_REVIEW_STATE.key === key ? SIGNAL_REVIEW_STATE.payload : null;
    if (!key) return;
    if (!payload || !Array.isArray(payload.records)) {
      alert('复盘数据还没加载完成，稍后再导出。');
      loadSignalReviews(true);
      return;
    }
    var records = payload.records.filter(function (item) { return item.symbol === key; });
    if (!records.length) {
      alert('当前币种暂无可导出的复盘记录。');
      return;
    }
    var headers = [
      'snapshot_at', 'symbol', 'advice_name', 'side', 'entry_price', 'stop_price', 'snapshot_price',
      'confidence', 'direction_score', 'execution_score', 'risk_gate', 'candle_state', 'trigger_status',
      'anchor_interval', 'trend_regime', 'atr_regime', 'boll_width_regime', 'boll_position_regime',
      'invalid_reason',
      '5m_reason', '5m_entry_reached', '5m_stop_hit', '5m_max_profit_pct', '5m_gross_max_profit_pct', '5m_max_drawdown_pct', '5m_outcome_pct', '5m_gross_outcome_pct', '5m_estimated_cost_pct',
      '15m_reason', '15m_entry_reached', '15m_stop_hit', '15m_max_profit_pct', '15m_gross_max_profit_pct', '15m_max_drawdown_pct', '15m_outcome_pct', '15m_gross_outcome_pct', '15m_estimated_cost_pct',
      '1h_reason', '1h_entry_reached', '1h_stop_hit', '1h_max_profit_pct', '1h_gross_max_profit_pct', '1h_max_drawdown_pct', '1h_outcome_pct', '1h_gross_outcome_pct', '1h_estimated_cost_pct'
    ];
    var rows = records.map(function (item) {
      var regime = item.market_regime || {};
      var invalid = item.evaluation && item.evaluation.invalid ? item.evaluation.invalid : {};
      return [
        item.snapshot_at || '',
        item.symbol || '',
        item.advice_name || '',
        item.side || '',
        item.entry_price || '',
        item.stop_price || '',
        item.snapshot_price || '',
        item.confidence || '',
        item.direction_score || '',
        item.execution_score || '',
        item.risk_gate || '',
        item.candle_state || '',
        item.trigger_status || '',
        regime.anchor_interval || '',
        regime.trend_regime || '',
        regime.atr_regime || '',
        regime.boll_width_regime || '',
        regime.boll_position_regime || '',
        invalid.failure_reason || item.failure_reason || '',
        reviewExportValue(item, '5m', 'failure_reason'),
        reviewExportValue(item, '5m', 'entry_reached'),
        reviewExportValue(item, '5m', 'stop_hit'),
        reviewExportValue(item, '5m', 'max_profit_pct'),
        reviewExportValue(item, '5m', 'gross_max_profit_pct'),
        reviewExportValue(item, '5m', 'max_drawdown_pct'),
        reviewExportValue(item, '5m', 'outcome_pct'),
        reviewExportValue(item, '5m', 'gross_outcome_pct'),
        reviewExportValue(item, '5m', 'estimated_cost_pct'),
        reviewExportValue(item, '15m', 'failure_reason'),
        reviewExportValue(item, '15m', 'entry_reached'),
        reviewExportValue(item, '15m', 'stop_hit'),
        reviewExportValue(item, '15m', 'max_profit_pct'),
        reviewExportValue(item, '15m', 'gross_max_profit_pct'),
        reviewExportValue(item, '15m', 'max_drawdown_pct'),
        reviewExportValue(item, '15m', 'outcome_pct'),
        reviewExportValue(item, '15m', 'gross_outcome_pct'),
        reviewExportValue(item, '15m', 'estimated_cost_pct'),
        reviewExportValue(item, '1h', 'failure_reason'),
        reviewExportValue(item, '1h', 'entry_reached'),
        reviewExportValue(item, '1h', 'stop_hit'),
        reviewExportValue(item, '1h', 'max_profit_pct'),
        reviewExportValue(item, '1h', 'gross_max_profit_pct'),
        reviewExportValue(item, '1h', 'max_drawdown_pct'),
        reviewExportValue(item, '1h', 'outcome_pct'),
        reviewExportValue(item, '1h', 'gross_outcome_pct'),
        reviewExportValue(item, '1h', 'estimated_cost_pct')
      ].map(csvCell).join(',');
    });
    var csv = '\ufeff' + headers.map(csvCell).join(',') + '\r\n' + rows.join('\r\n');
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'signal-review-' + key + '-' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function setSignalReviewEvaluateButton(loading) {
    var btn = document.getElementById('signal-review-evaluate');
    if (!btn) return;
    btn.disabled = !!loading;
    btn.textContent = loading ? '评估中...' : '评估';
  }
  function forceEvaluateSignalReviews() {
    setSignalReviewEvaluateButton(true);
    fetch('api/signal-reviews/evaluate', { method: 'POST', cache: 'no-store' })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (payload) {
          if (!res.ok) throw new Error(payload.error || (payload.evaluation && payload.evaluation.reason) || ('HTTP ' + res.status));
          return payload;
        });
      })
      .then(function () {
        loadSignalReviews(true);
      })
      .catch(function (err) {
        alert('复盘评估触发失败：' + err.message);
      })
      .then(function () {
        setSignalReviewEvaluateButton(false);
      });
  }
  function bindSignalReviewEvaluate() {
    var btn = document.getElementById('signal-review-evaluate');
    if (!btn || btn._bound) return;
    btn._bound = true;
    btn.addEventListener('click', forceEvaluateSignalReviews);
  }
  function bindSignalReviewExport() {
    var btn = document.getElementById('signal-review-export');
    if (!btn || btn._bound) return;
    btn._bound = true;
    btn.addEventListener('click', exportSignalReviewCsv);
  }
  function renderSignalReview() {
    var host = document.getElementById('signal-review');
    if (!host) return;
    var r = cur();
    if (!r) {
      host.innerHTML = '<div class="review-empty">暂无币种，无法复盘。</div>';
      return;
    }
    var key = r.symbol;
    if (SIGNAL_REVIEW_STATE.loading && SIGNAL_REVIEW_STATE.key === key && !SIGNAL_REVIEW_STATE.payload) {
      host.innerHTML = '<div class="review-empty">正在读取实盘复盘样本...</div>';
      return;
    }
    if (SIGNAL_REVIEW_STATE.error && SIGNAL_REVIEW_STATE.key === key && !SIGNAL_REVIEW_STATE.payload) {
      host.innerHTML = '<div class="review-empty">复盘数据读取失败：' + htmlSafe(SIGNAL_REVIEW_STATE.error) + '</div>';
      return;
    }
    var payload = SIGNAL_REVIEW_STATE.key === key ? SIGNAL_REVIEW_STATE.payload : null;
    if (!payload) {
      host.innerHTML = '<div class="review-empty">暂无实盘复盘样本。新策略刷新后会自动记录，5分钟后开始出现第一批结果。</div>';
      return;
    }
    var stats = payload.stats || {};
    var per = stats.per_horizon || {};
    var overallQuality = reviewOverallQuality(per);
    var duePending = Number(stats.due_pending || 0);
    var qualityText = overallQuality.text +
      (Number(stats.invalid_records || 0) ? ' 无效信号 ' + Number(stats.invalid_records || 0) + ' 条，优先检查入场/止损合法性。' : '') +
      (duePending ? ' 有 ' + duePending + ' 条信号已到期但尚未完成评估，等待后台复盘刷新。' : '');
    qualityText += reviewEvaluationText(payload.evaluation);
    var summary = ['5m', '15m', '1h'].map(function (h) {
      var s = per[h] || {};
      var sampleCount = Number(s.sample_count || 0);
      var triggeredCount = Number(s.triggered_count || 0);
      var quality = reviewSampleQualityByCount(sampleCount);
      var hitCls = sampleCount >= 20 && triggeredCount >= 10 && Number(s.hit_rate_pct || 0) >= 55 ? 'good' : Number(s.hit_rate_pct || 0) ? 'warn' : '';
      var stopCls = Number(s.stop_rate_pct || 0) >= 35 ? 'bad' : Number(s.stop_rate_pct || 0) ? 'warn' : '';
      return '<div class="review-stat">' +
        '<div class="rs-head"><span class="rs-h">' + reviewHorizonLabel(h) + '</span><span class="rs-n">样本 ' + sampleCount + ' / 触发 ' + triggeredCount + '</span></div>' +
        '<div class="rs-main">' +
          '<div><div class="rs-k">命中</div><div class="rs-v ' + hitCls + '">' + Number(s.hit_rate_pct || 0).toFixed(0) + '%</div></div>' +
          '<div><div class="rs-k">止损</div><div class="rs-v ' + stopCls + '">' + Number(s.stop_rate_pct || 0).toFixed(0) + '%</div></div>' +
          '<div><div class="rs-k">未触发</div><div class="rs-v warn">' + Number(s.not_triggered_rate_pct || 0).toFixed(0) + '%</div></div>' +
          '<div><div class="rs-k">净最大利</div><div class="rs-v good">' + reviewPct(s.avg_max_profit_pct || 0) + '</div></div>' +
          '<div><div class="rs-k">最大撤</div><div class="rs-v bad">' + reviewPct(s.avg_max_drawdown_pct || 0) + '</div></div>' +
          '<div><div class="rs-k">净结果</div><div class="rs-v">' + reviewPct(s.avg_outcome_pct || 0) + '</div></div>' +
        '</div>' +
        '<div class="review-quality ' + quality.cls + '">' + quality.label + ' · ' + quality.text + '</div>' +
      '</div>';
    }).join('');
    var segmentsHtml = renderReviewSegments(stats.segments);
    var calibrationHtml = renderReviewCalibration(stats.calibration);
    var records = (payload.records || []).filter(function (x) { return x.symbol === key; }).slice(0, 18);
    var list = records.map(function (x) {
      var done = latestDoneReview(x);
      var reason = done ? (done.failure_reason || x.failure_reason || '') : (x.failure_reason || 'pending');
      var reasonCls = done ? reviewReasonClass(reason) : 'warn';
      var statusText = done ? reviewReasonText(reason) : '等待到期';
      var horizon = done ? reviewHorizonLabel(done._horizon) : '待评估';
      var triggerText = done
        ? (done.entry_reached ? '已触发' : '未触发')
        : '未到评估时间';
      var stopText = done && done.stop_hit ? '触及止损' : '未止损';
      var priceText = '入场 <b>' + fmtPrice(Number(x.entry_price || 0)) + '</b> / 止损 <b>' + fmtPrice(Number(x.stop_price || 0)) + '</b>';
      var resultText = done
        ? '净最大盈利 <b>' + reviewPct(done.max_profit_pct || 0) + '</b>，最大回撤 <b>' + reviewPct(done.max_drawdown_pct || 0) + '</b>，净结果 <b>' + reviewPct(done.outcome_pct || 0) + '</b>'
        : '等待 5m/15m/1h 到期后后台自动评估';
      return '<div class="review-row">' +
        '<div class="review-time">' + (x.snapshot_at_ms ? relativeSnapshot(Number(x.snapshot_at_ms)) : '--') + '<br>' + htmlSafe(x.advice_name || '') + '</div>' +
        '<div class="review-side ' + htmlSafe(x.side || '') + '">' + reviewSideText(x.side) + '<br><span style="color:var(--muted-strong)">分 ' + (x.execution_score || x.confidence || 0) + '</span></div>' +
        '<div class="review-body">' + priceText + '<br>' + resultText +
          '<div class="review-tags">' +
            '<span class="review-tag ' + reasonCls + '">' + htmlSafe(horizon + ' ' + statusText) + '</span>' +
            '<span class="review-tag">' + htmlSafe(triggerText) + '</span>' +
            '<span class="review-tag ' + (done && done.stop_hit ? 'bad' : '') + '">' + htmlSafe(stopText) + '</span>' +
            '<span class="review-tag">' + htmlSafe(x.risk_gate || '风控-') + '</span>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('');
    host.innerHTML = '<div class="review-note ' + overallQuality.cls + '"><b>' + overallQuality.label + '</b><span>' + qualityText + '</span></div>' +
      '<div class="review-summary">' + summary + '</div>' +
      segmentsHtml +
      calibrationHtml +
      (records.length ? '<div class="review-list">' + list + '</div>' : '<div class="review-empty">暂无当前币种复盘记录。等待下一次策略刷新写入样本。</div>');
  }
  function loadSignalReviews(force) {
    var r = cur();
    if (!r || !r.symbol) return;
    var key = r.symbol;
    if (SIGNAL_REVIEW_STATE.loading && SIGNAL_REVIEW_STATE.key === key) return;
    if (!force && SIGNAL_REVIEW_STATE.key === key && Date.now() - SIGNAL_REVIEW_STATE.lastFetch < 30000) return;
    if (SIGNAL_REVIEW_STATE.key !== key) SIGNAL_REVIEW_STATE.payload = null;
    SIGNAL_REVIEW_STATE.key = key;
    SIGNAL_REVIEW_STATE.loading = true;
    SIGNAL_REVIEW_STATE.error = '';
    var requestSeq = ++SIGNAL_REVIEW_REQUEST_SEQ;
    renderSignalReview();
    fetch('api/signal-reviews?symbols=' + encodeURIComponent(key) + '&limit=240', { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        if (requestSeq !== SIGNAL_REVIEW_REQUEST_SEQ || !cur() || cur().symbol !== key) return;
        SIGNAL_REVIEW_STATE = { key: key, payload: payload, loading: false, error: '', lastFetch: Date.now() };
        renderSignalReview();
      })
      .catch(function (err) {
        if (requestSeq !== SIGNAL_REVIEW_REQUEST_SEQ || !cur() || cur().symbol !== key) return;
        SIGNAL_REVIEW_STATE.loading = false;
        SIGNAL_REVIEW_STATE.error = err.message;
        SIGNAL_REVIEW_STATE.lastFetch = Date.now();
        renderSignalReview();
      });
  }
  function updateRealtimeSignal() {
    var r = cur();
    var snap = signalSnapshot(r);
    if (!r || !snap) return;
    var risk = riskLevel(r);
    var pos = riskAdjustedPosition(snap.position, risk, snap);
    var bestAdvice = snap.advice;
    var state = getPositionState(r.symbol);
    var livePriceEl = document.querySelector('#signal-banner .js-signal-live-price');
    var entryLabelEl = document.querySelector('#signal-banner .js-signal-entry-label');
    var entryEl = document.querySelector('#signal-banner .js-signal-entry');
    var distanceEl = document.querySelector('#signal-banner .js-signal-distance');
    var stopEl = document.querySelector('#signal-banner .js-signal-stop');
    var posEl = document.querySelector('#signal-banner .js-signal-position');
    var riskEl = document.querySelector('#signal-banner .js-risk-level');
    var alertEl = document.querySelector('#signal-banner .js-signal-alert');
    var topLineEl = document.querySelector('#signal-banner .js-signal-topline');
    var depthEl = document.querySelector('#signal-banner .js-signal-depth');
    var depthSizeEl = document.querySelector('#signal-banner .js-signal-depth-size');
    var sbEl = document.querySelector('#signal-banner .sb');
    if (sbEl) {
      sbEl.className = 'sb risk-' + risk.key + (bestAdvice && bestAdvice.candle_state === '实时K线' ? ' realtime-prejudge' : '');
    }
    if (livePriceEl) livePriceEl.textContent = realtimePriceText(r);
    if (entryLabelEl) entryLabelEl.textContent = snap.entryLabel;
    if (entryEl && snap.entry != null) entryEl.textContent = fmtPrice(snap.entry);
    if (distanceEl) distanceEl.textContent = snap.entryDistance || '';
    if (stopEl && snap.stop != null) stopEl.textContent = fmtPrice(snap.stop);
    if (posEl && pos) {
      posEl.textContent = pos.text;
      posEl.style.color = pos.color;
      posEl.title = pos.note || '';
    }
    if (depthEl && r._depth_imbalance != null && isFinite(Number(r._depth_imbalance))) {
      depthEl.textContent = Number(r._depth_imbalance).toFixed(2);
    }
    if (depthSizeEl && (r._bid_depth_top5_usd != null || r._ask_depth_top5_usd != null)) {
      depthSizeEl.textContent = '买' + fmtVol(Number(r._bid_depth_top5_usd || 0)) + '/卖' + fmtVol(Number(r._ask_depth_top5_usd || 0));
    }
    if (riskEl) {
      riskEl.className = 'risk-pill js-risk-level ' + risk.key;
      riskEl.textContent = risk.label;
      riskEl.title = risk.text;
    }
    if (alertEl) {
      var alert = signalAlert(r, snap);
      alertEl.className = 'sb-alert js-signal-alert' + (alert.text ? ' show ' + alert.cls : '');
      alertEl.textContent = alert.text;
      maybeNotifySignalAlert(r, alert);
    }
    updateSignalAlertButton();
    if (topLineEl) {
      var topLine = signalTopline(risk, bestAdvice, snap, state);
      topLineEl.className = 'sb-topline js-signal-topline' + (topLine ? ' show ' + topLine.cls : '');
      topLineEl.textContent = topLine ? topLine.text : '';
    }
  }
  function currentSymbolsKey() {
    return DATA.map(function (r) { return r.symbol; }).sort().join(',');
  }
  function startRealtimePrices() {
    if (!window.EventSource) return;
    var key = currentSymbolsKey();
    if (key === realtimeKey) return;
    if (realtimeSource) {
      realtimeSource.close();
      realtimeSource = null;
    }
    realtimeKey = key;
    if (!key) return;
    realtimeSource = new EventSource('api/realtime-prices?symbols=' + encodeURIComponent(key));
    realtimeSource.onmessage = function (ev) {
      try {
        var payload = JSON.parse(ev.data || '{}');
        (payload.prices || []).forEach(applyRealtimePrice);
      } catch (e) {}
    };
    realtimeSource.onerror = function () {
      updateLiveBadge(false);
    };
  }
  function applyRealtimePrice(item) {
    if (!item || !item.symbol || item.price == null) return;
    var price = Number(item.price);
    if (!isFinite(price)) return;
    var ms = Number(item.event_ms || item.received_ms || Date.now());
    DATA.forEach(function (r) {
      if (r.symbol !== item.symbol) return;
      r.last = price;
      r._price_snapshot_ms = ms;
      if (item.bid != null) r._price_bid = item.bid;
      if (item.ask != null) r._price_ask = item.ask;
      if (item.depth_imbalance != null) r._depth_imbalance = item.depth_imbalance;
      if (item.bid_depth_top5_usd != null) r._bid_depth_top5_usd = item.bid_depth_top5_usd;
      if (item.ask_depth_top5_usd != null) r._ask_depth_top5_usd = item.ask_depth_top5_usd;
      if (item.depth_ladder != null) r._depth_ladder = item.depth_ladder;
    });
    if (cur() && cur().symbol === item.symbol) {
      var el = document.querySelector('#kpi-main .price');
      if (el) {
        el.textContent = fmtPrice(price);
        el.title = '实时价格';
      }
      updateRealtimeSignal();
      refreshOpeningGuardSoon();
      renderDepthDom();
      setGen();
      updateLiveBadge(true);
    }
  }
  function refreshStrategyAnalysis() {
    if (strategyRefreshBusy || !DATA.length) return;
    var wantedSymbols = wantedRefreshSymbols();
    var symbols = wantedSymbols.join(',');
    if (!wantedSymbols.length) return;
    strategyRefreshBusy = true;
    nextStrategyRefreshAt = Date.now();
    setGen();
    fetch('api/market?symbols=' + encodeURIComponent(symbols), { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        var fresh = stampReports(payload.data || [], payload.generated_at);
        if (!fresh.length) throw new Error('empty data');
        DATA = mergeFreshReports(fresh);
        applyRemovedSyms();
        GEN = payload.generated_at || GEN;
        if (!DATA.some(function (r) { return r.symbol === currentSymbol; })) {
          currentSymbol = DATA.length ? DATA[0].symbol : '';
        }
        LIVE = true;
        updateLiveBadge(true, payload);
        recordSignalHistory('自动刷新');
        render();
      })
      .catch(function (err) {
        console.warn('[dashboard] auto strategy refresh failed:', err.message);
      })
      .then(function () {
        strategyRefreshBusy = false;
        nextStrategyRefreshAt = Date.now() + AUTO_STRATEGY_REFRESH_MS;
        setGen();
      });
  }
  function startStrategyRefreshTimer() {
    if (strategyRefreshTimer) return;
    nextStrategyRefreshAt = Date.now() + AUTO_STRATEGY_REFRESH_MS;
    setGen();
    strategyRefreshTimer = setInterval(refreshStrategyAnalysis, AUTO_STRATEGY_REFRESH_MS);
  }

  /* ---------- symbol switcher ---------- */
  function cur() {
    // returns the report object for currentSymbol (or first if not set)
    if (!DATA.length) return null;
    if (!currentSymbol) currentSymbol = DATA[0].symbol;
    for (var i = 0; i < DATA.length; i++) { if (DATA[i].symbol === currentSymbol) return DATA[i]; }
    return DATA[0];
  }
  var DEFAULT_SYMS = (window.MARKET_DATA || []).map(function (r) { return r.symbol; });
  // localStorage: save / load / remove custom symbols
  function saveCustomSyms() {
    var custom = [];
    DATA.forEach(function (r) {
      var sym = normalizeClientSymbol(r && r.symbol);
      if (sym && DEFAULT_SYMS.indexOf(sym) < 0 && custom.indexOf(sym) < 0) custom.push(sym);
    });
    try { localStorage.setItem(LS_KEY, JSON.stringify(custom)); } catch (e) {}
    rememberSymbols(DATA.map(function (r) { return r && r.symbol; }), true);
    saveServerPreferences({ custom_symbols: custom });
  }
  function loadCustomSyms() {
    try {
      var v = localStorage.getItem(LS_KEY);
      var arr = v ? JSON.parse(v) : [];
      if (!Array.isArray(arr)) return [];
      var out = [];
      arr.forEach(function (item) {
        var sym = normalizeClientSymbol(item);
        if (sym && out.indexOf(sym) < 0) out.push(sym);
      });
      return out;
    } catch (e) { return []; }
  }
  function loadRemovedSyms() {
    try {
      var v = localStorage.getItem(REMOVED_KEY);
      var arr = v ? JSON.parse(v) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  function saveRemovedSyms(symbols) {
    try { localStorage.setItem(REMOVED_KEY, JSON.stringify(symbols || [])); } catch (e) {}
    saveServerPreferences({ removed_symbols: symbols || [] });
  }
  function applyRemovedSyms() {
    var removed = loadRemovedSyms();
    if (!removed.length) return;
    DATA = DATA.filter(function (r) { return removed.indexOf(r.symbol) < 0; });
    if (currentSymbol && removed.indexOf(currentSymbol) >= 0) currentSymbol = DATA.length ? DATA[0].symbol : '';
  }
  function forgetRemovedSym(sym) {
    var removed = loadRemovedSyms().filter(function (s) { return s !== sym; });
    saveRemovedSyms(removed);
  }

  // refresh a single symbol's data in-place
  function refreshSymbol(sym, onDone) {
    fetch('api/market?symbol=' + encodeURIComponent(sym), { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        var arr = stampReports(payload.data || [], payload.generated_at);
        if (!arr.length) throw new Error('未取到数据');
        // replace existing report for this symbol
        DATA = DATA.map(function (r) { return r.symbol === sym ? arr[0] : r; });
        GEN = payload.generated_at || GEN;
        updateLiveBadge(true, payload);
        recordSignalHistory('手动刷新');
        render();
        if (onDone) onDone(null);
      })
      .catch(function (err) { if (onDone) onDone(err); })
      .then(function () { setGen(); });
  }

  function renderSymbolTabs() {
    var host = document.getElementById('symbol-tabs');
    if (!host) return;
    var html = '';
    DATA.forEach(function (r) {
      var active = r.symbol === currentSymbol ? ' active' : '';
      html += '<button class="sym-tab' + active + '" data-sym="' + r.symbol + '">' +
        r.symbol +
        '<span class="rf" data-rf="' + r.symbol + '" title="单独刷新此币种">↻</span>' +
        '<span class="rm" data-rm="' + r.symbol + '" title="移除">×</span>' +
        '</button>';
    });
    host.innerHTML = html;
    // symbol switch
    var btns = host.querySelectorAll('.sym-tab');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', function (e) {
        if (e.target.classList.contains('rm') || e.target.classList.contains('rf')) return;
        currentSymbol = this.getAttribute('data-sym');
        renderSymbolTabs();
        render();
      });
    }
    // remove buttons
    var rms = host.querySelectorAll('.rm');
    for (var j = 0; j < rms.length; j++) {
      rms[j].addEventListener('click', function (e) {
        e.stopPropagation();
        var sym = this.getAttribute('data-rm');
        removeSymbol(sym);
      });
    }
    // refresh buttons
    var rfs = host.querySelectorAll('.rf');
    for (var k = 0; k < rfs.length; k++) {
      rfs[k].addEventListener('click', function (e) {
        e.stopPropagation();
        var sym = this.getAttribute('data-rf');
        var el = this;
        el.classList.add('spin');
        el.style.color = 'var(--accent)';
        refreshSymbol(sym, function (err) {
          el.classList.remove('spin');
          el.style.color = '';
        });
      });
    }
    bindAddSymbol();
  }

  function removeSymbol(sym) {
    rememberSymbols([sym], true);
    var removed = loadRemovedSyms();
    if (removed.indexOf(sym) < 0) {
      removed.push(sym);
      saveRemovedSyms(removed);
    }
    DATA = DATA.filter(function (r) { return r.symbol !== sym; });
    if (currentSymbol === sym) currentSymbol = DATA.length ? DATA[0].symbol : '';
    saveCustomSyms();
    renderSymbolTabs();
    render();
  }

  function setAddStatus(msg, cls) {
    var el = document.getElementById('add-sym-status');
    if (!el) return;
    el.textContent = msg;
    el.className = 'add-sym-status' + (cls ? ' ' + cls : '');
  }

  function bindAddSymbol() {
    var btn = document.getElementById('add-sym-btn');
    var input = document.getElementById('add-sym-input');
    renderSymbolHistorySuggestions();
    if (!btn || !input || btn._bound) return;
    btn._bound = true;
    function doAdd() { addSymbol(input.value, 'input'); }
    btn.addEventListener('click', doAdd);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') doAdd(); });
  }

  function renderSymbolHistorySuggestions() {
    var host = document.getElementById('symbol-history-suggestions');
    if (!host) return;
    var candidates = symbolHistoryCandidates();
    if (!candidates.length) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = '<span class="sym-history-label">最近币种</span>' + candidates.map(function (sym) {
      return '<button class="sym-history-btn" type="button" data-history-symbol="' + sym + '" title="添加 ' + sym + '">' + sym.replace('USDT', '') + '</button>';
    }).join('');
    var btns = host.querySelectorAll('[data-history-symbol]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', function () {
        addSymbol(this.getAttribute('data-history-symbol'), 'history');
      });
    }
  }

  function addSymbol(rawSymbol, source) {
    var raw = normalizeClientSymbol(rawSymbol);
    if (!raw) { setAddStatus('请输入币种', 'err'); return; }
    if (DATA.some(function (r) { return r.symbol === raw; })) {
      currentSymbol = raw;
      rememberSymbols([raw], true);
      renderSymbolTabs();
      render();
      setAddStatus(raw + ' 已打开', 'ok');
      return;
    }
    var btn2 = document.getElementById('add-sym-btn');
    var input = document.getElementById('add-sym-input');
    if (btn2) btn2.disabled = true;
    setAddStatus('正在获取 ' + raw + ' 实时数据…', '');
    showLoading('正在获取 ' + raw + ' …');
    fetch('api/market?symbol=' + encodeURIComponent(raw), { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        var arr = stampReports(payload.data || [], payload.generated_at);
        if (!arr.length) throw new Error('未取到数据');
        arr.forEach(function (item) {
          if (!DATA.some(function (r) { return r.symbol === item.symbol; })) DATA.push(item);
        });
        GEN = payload.generated_at || GEN;
        updateLiveBadge(true, payload);
        forgetRemovedSym(arr[0].symbol);
        rememberSymbols([arr[0].symbol], true);
        currentSymbol = arr[0].symbol;
        saveCustomSyms();
        recordSignalHistory(source === 'history' ? '历史币种恢复' : '添加币种');
        renderSymbolTabs();
        render();
        setAddStatus(raw + ' 添加成功 ✓', 'ok');
        if (input) input.value = '';
      })
      .catch(function (err) {
        setAddStatus('添加失败: ' + err.message, 'err');
      })
      .then(function () {
        if (btn2) btn2.disabled = false;
        hideLoading();
      });
  }

  /* ---------- P0: main signal banner ---------- */
  function renderSignal() {
    var r = cur(); if (!r) return;
    var host = document.getElementById('signal-banner'); if (!host) return;
    var snap = signalSnapshot(r);
    var bestAdvice = snap.advice;
    var signalBias = (bestAdvice && bestAdvice.bias) || r.bias;
    var bc = biasClass(signalBias);
    var lightClass = bc === 'bull' ? 'green' : bc === 'bear' ? 'red' : 'yellow';
    var risk = riskLevel(r);
    if (risk.key === 'block') lightClass = 'red';
    else if (bestAdvice && bestAdvice.candle_state === '实时K线') lightClass = 'yellow';
    var pos = riskAdjustedPosition(snap.position, risk, snap);
    var alert = signalAlert(r, snap);
    var state = getPositionState(r.symbol);
    var topLine = signalTopline(risk, bestAdvice, snap, state);
    var title = r.symbol.replace('USDT', '') + ' ' + (bestAdvice ? bestAdvice.name : '当前') + '：' + biasPlain(signalBias);
    var reason = bestAdvice ? bestAdvice.action : (r.summary || '');
    // 主行 5 核心:实时价 / 方向 / 开仓分 / 触发状态 / 建议仓位
    var majorHtml = '<div class="sb-act major">实时价<b class="js-signal-live-price" style="color:' + C.accent + '">' + realtimePriceText(r) + '</b></div>';
    if (bestAdvice) {
      var entryColor = snap.side === 'short' ? C.bear : snap.side === 'long' ? C.bull : C.accent;
      majorHtml += '<div class="sb-act major">方向分<b style="color:' + C.accent + '">' + Math.round(snap.directionScore || 0) + '</b></div>';
      majorHtml += '<div class="sb-act major">开仓分<b style="color:' + (snap.executionScore >= 60 ? C.accent2 : C.warn) + '">' + Math.round(snap.executionScore || 0) + '</b></div>';
      majorHtml += '<div class="sb-act major">触发<b style="color:' + (bestAdvice.trigger_check && bestAdvice.trigger_check.status === 'confirmed' ? C.accent2 : C.warn) + '">' + triggerLabel(bestAdvice.trigger_check) + '</b></div>';
      majorHtml += '<div class="sb-act major">建议仓位<b class="js-signal-position" title="' + (pos.note || '') + '" style="color:' + pos.color + '">' + pos.text + '</b></div>';
      // 次行辅助:K线/风控/回测/盘口/入场/止损/周期
      var minorHtml = '<div class="sb-act minor">K线<b style="color:' + (bestAdvice.candle_state === '已完成K线' ? C.accent2 : C.warn) + '">' + (bestAdvice.candle_state || '-') + '</b></div>';
      minorHtml += '<div class="sb-act minor">风控<b style="color:' + (bestAdvice.risk_gate === '正常' ? C.accent2 : C.warn) + '">' + gateText(bestAdvice.risk_gate) + '</b></div>';
      minorHtml += '<div class="sb-act minor">回测<b style="color:' + C.accent + '">' + backtestSummary(bestAdvice.backtest) + '</b></div>';
      var depthImbalance = r._depth_imbalance != null ? r._depth_imbalance : r.signal_quality && r.signal_quality.depth_imbalance;
      var bidDepth = r._bid_depth_top5_usd != null ? r._bid_depth_top5_usd : r.signal_quality && r.signal_quality.bid_depth_top5_usd;
      var askDepth = r._ask_depth_top5_usd != null ? r._ask_depth_top5_usd : r.signal_quality && r.signal_quality.ask_depth_top5_usd;
      if (depthImbalance != null && isFinite(Number(depthImbalance))) {
        minorHtml += '<div class="sb-act minor">盘口<b class="js-signal-depth" style="color:' + C.accent + '">' + Number(depthImbalance).toFixed(2) + '</b><span class="minor js-signal-depth-size">买' + fmtVol(Number(bidDepth || 0)) + '/卖' + fmtVol(Number(askDepth || 0)) + '</span></div>';
      }
      minorHtml += '<div class="sb-act minor"><span class="js-signal-entry-label">' + snap.entryLabel + '</span><b class="js-signal-entry" style="color:' + entryColor + '">' + fmtPrice(snap.entry) + '</b><span class="minor js-signal-distance">' + snap.entryDistance + '</span></div>';
      minorHtml += '<div class="sb-act minor">止损<b class="js-signal-stop" style="color:' + C.warn + '">' + fmtPrice(snap.stop) + '</b></div>';
      minorHtml += '<div class="sb-act minor">周期<b>' + bestAdvice.name + '</b></div>';
    } else {
      var minorHtml = '';
    }
    var states = ['空仓', '多单', '空单'];
    var stateHtml = '<div class="pos-state" data-symbol="' + r.symbol + '"><span class="ps-label">当前状态</span>' +
      states.map(function (s) { return '<button class="pos-btn ' + (state === s ? 'active' : '') + '" data-state="' + s + '">' + s + '</button>'; }).join('') +
      '</div>';
    var sbClass = 'sb risk-' + risk.key + (bestAdvice && bestAdvice.candle_state === '实时K线' ? ' realtime-prejudge' : '');
    host.innerHTML =
      '<div class="' + sbClass + '">' +
        '<div class="light ' + lightClass + '"></div>' +
        '<div class="sb-body">' +
          '<div class="sb-topline js-signal-topline' + (topLine ? ' show ' + topLine.cls : '') + '">' + (topLine ? topLine.text : '') + '</div>' +
          '<div class="sb-title">' + title + '</div>' +
          '<div class="sb-reason">' + reason + '</div>' +
          '<div class="sb-actions">' + majorHtml + '</div>' +
          (minorHtml ? '<div class="sb-actions-minor">' + minorHtml + '</div>' : '') +
          '<div class="sb-meta"><span class="risk-pill js-risk-level ' + risk.key + '" title="' + risk.text + '">' + risk.label + '</span>' + stateHtml + '<button type="button" class="alert-toggle js-signal-alert-toggle">' + alertButtonText() + '</button></div>' +
          '<div class="sb-alert js-signal-alert' + (alert.text ? ' show ' + alert.cls : '') + '">' + alert.text + '</div>' +
        '</div>' +
      '</div>';
    bindPositionStateButtons(host);
    bindSignalAlertToggle(host);
    maybeNotifySignalAlert(r, alert);
  }

  /* ---------- P1: indicator traffic lights ---------- */
  function renderIndicatorLights() {
    var r = cur(); if (!r) return;
    var host = document.getElementById('indicator-lights'); if (!host) return;
    // use 1h timeframe as representative
    var ind = r.indicators['1h'];
    var fr = r.funding_rate * 100;
    var lights = [
      {
        name: 'RSI', val: ind.rsi14.toFixed(1),
        color: ind.rsi14 >= 70 ? 'red' : ind.rsi14 >= 55 ? 'green' : ind.rsi14 <= 30 ? 'green' : ind.rsi14 <= 45 ? 'red' : 'yellow',
        plain: ind.rsi14 >= 70 ? '涨太多，可能要回调（超买）' : ind.rsi14 <= 30 ? '跌太多，可能要反弹（超卖）' : ind.rsi14 >= 55 ? '偏强，买方占优' : ind.rsi14 <= 45 ? '偏弱，卖方占优' : '买卖力量均衡'
      },
      {
        name: '布林位置', val: ind.boll_position_pct.toFixed(0) + '%',
        color: ind.boll_position_pct >= 90 ? 'red' : ind.boll_position_pct <= 10 ? 'green' : ind.boll_position_pct >= 60 ? 'yellow' : ind.boll_position_pct <= 40 ? 'yellow' : 'green',
        plain: ind.boll_position_pct >= 90 ? '靠近顶部，偏贵小心' : ind.boll_position_pct <= 10 ? '靠近底部，偏便宜' : ind.boll_position_pct >= 60 ? '中上区间，偏强' : ind.boll_position_pct <= 40 ? '中下区间，偏弱' : '中间位置，方向不明'
      },
      {
        name: 'ATR 波动率', val: ind.atr14_pct.toFixed(2) + '%',
        color: ind.atr14_pct >= 8 ? 'red' : ind.atr14_pct >= 3 ? 'yellow' : 'green',
        plain: ind.atr14_pct >= 8 ? '波动很大，减小仓位宽止损' : ind.atr14_pct >= 3 ? '正常波动' : '波动很小，比较平静'
      },
      {
        name: 'EMA偏离', val: (ind.mt_pct_vs_ema20 >= 0 ? '+' : '') + ind.mt_pct_vs_ema20.toFixed(2) + '%',
        color: ind.mt_pct_vs_ema20 >= 3 ? 'yellow' : ind.mt_pct_vs_ema20 >= 0 ? 'green' : ind.mt_pct_vs_ema20 <= -3 ? 'yellow' : 'red',
        plain: ind.mt_pct_vs_ema20 >= 0 ? '在均线上方，短期偏强' : '在均线下方，短期偏弱'
      },
      {
        name: '量能', val: ind.vol_ratio_20.toFixed(2) + 'x',
        color: ind.vol_ratio_20 >= 1.5 ? 'green' : ind.vol_ratio_20 >= 0.8 ? 'yellow' : 'red',
        plain: ind.vol_ratio_20 >= 1.5 ? '放量，有人参与' : ind.vol_ratio_20 >= 0.8 ? '量能正常' : '缩量，没人理'
      },
      {
        name: '资金费率', val: fr.toFixed(4) + '%',
        color: fr > 0.05 ? 'yellow' : fr < -0.05 ? 'green' : 'green',
        plain: fr > 0.05 ? '多头拥挤，多头付钱给空头' : fr < -0.05 ? '空头拥挤，空头付钱给多头' : '多空均衡，费率正常'
      }
    ];
    var html = lights.map(function (l) {
      return '<div class="ind-cell">' +
        '<div class="ind-dot ' + l.color + '"></div>' +
        '<div class="ind-info">' +
          '<div class="ind-name">' + l.name + '</div>' +
          '<div class="ind-val">' + l.val + '</div>' +
          '<div class="ind-plain">' + l.plain + '</div>' +
        '</div>' +
      '</div>';
    }).join('');
    host.innerHTML = html;
  }

  /* ---------- KPI cards ---------- */
  function renderKpi() {
    var r = cur(); if (!r) return;
    var host = document.getElementById('kpi-main');
    if (!host) return;
    var key = r.symbol.toLowerCase().replace('usdt', '');
    var bc = biasClass(r.bias);
    var confColor = bc === 'bull' ? C.bull : bc === 'bear' ? C.bear : C.warn;
    var directionScore = directionScoreOf(null, r);
    var executionScore = executionScoreOf(null, r);
    // multi-timeframe change: 15m / 1h / 4h / 8h / 24h
    var changes = [
      { label: '15M', val: r.indicators['15m'].change_pct },
      { label: '1H', val: r.indicators['1h'].change_pct },
      { label: '4H', val: r.indicators['4h'].change_pct },
      { label: '8H', val: r.indicators['8h'].change_pct },
      { label: '24H', val: r.pct_24h }
    ];
    var chgHtml = changes.map(function (c) {
      var up = c.val >= 0;
      var col = up ? C.bull : C.bear;
      return '<div class="chg-cell">' +
        '<span class="chg-l">' + c.label + '</span>' +
        '<span class="chg-v" style="color:' + col + '">' + fmtPct(c.val) + '</span>' +
      '</div>';
    }).join('');
    // 资金费率大显示 + 倒计时(Binance USD-M 每8h结算:00/08/16 UTC)
    var fr = r.funding_rate * 100;
    var frAbs = Math.abs(fr);
    var frCls = frAbs >= 0.1 ? 'danger' : frAbs >= 0.05 ? 'warn' : '';
    var frColor = frAbs >= 0.1 ? C.bear : frAbs >= 0.05 ? C.warn : C.accent2;
    var frPlain = fr > 0.05 ? '多头拥挤，多头付钱给空头' : fr < -0.05 ? '空头拥挤，空头付钱给多头' : '多空均衡，费率正常';
    var cdMs = fundingCountdownMs();
    var cdText = formatHMS(cdMs);
    host.innerHTML =
      '<div class="kpi-top">' +
        '<div class="sym"><span class="base">' + key.toUpperCase() + '</span><span class="quote">USDT</span></div>' +
        biasBadge(r.bias) +
      '</div>' +
      '<div class="price-line">' +
        '<span class="price">' + fmtPrice(r.last) + '</span>' +
      '</div>' +
      '<div class="chg-row">' + chgHtml + '</div>' +
      '<div class="summary">' + r.summary + '</div>' +
      '<div class="mini-grid">' +
        mini('24H 高', fmtPrice(r.high_24h)) +
        mini('24H 低', fmtPrice(r.low_24h)) +
        mini('24H 额', fmtVol(r.quote_volume_24h)) +
      '</div>' +
      '<div class="funding-box ' + frCls + '">' +
        '<div class="funding-main">' +
          '<div class="funding-label">资金费率</div>' +
          '<div class="funding-val" style="color:' + frColor + '">' + fr.toFixed(4) + '%</div>' +
          '<div class="funding-plain">' + frPlain + '</div>' +
        '</div>' +
        '<div class="funding-countdown">' +
          '<div class="fc-next">下次结算</div>' +
          '<div class="fc-time">' + cdText + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="conf-wrap">' +
        '<div id="gauge-main"></div>' +
        '<div class="conf-meta">' +
          '<div class="cl">CONFIDENCE</div>' +
          '<div class="cv" style="color:' + confColor + '">' + executionScore + '<span style="font-size:14px;color:' + C.muted + '">/100</span></div>' +
          '<div class="cb">开仓分 · 方向' + directionScore + '</div>' +
        '</div>' +
      '</div>';
    var g = echarts.init(document.getElementById('gauge-main'), null, { renderer: 'svg' });
    charts.push(g);
    g.setOption({
      animation: false,
      series: [{
        type: 'gauge', radius: '96%', startAngle: 220, endAngle: -40,
        min: 0, max: 100,
        progress: { show: true, width: 9, itemStyle: { color: confColor } },
        axisLine: { lineStyle: { width: 9, color: [[0.5, C.rule], [1, C.rule]] } },
          pointer: { show: false },
          axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
          detail: { valueAnimation: false, formatter: '{value}', fontSize: 22, fontFamily: FONT, color: confColor, offsetCenter: [0, '2%'] },
          data: [{ value: executionScore }]
        }]
      });
  }
  function fundingCountdownMs() {
    // Binance USD-M 每8h结算:00:00 / 08:00 / 16:00 UTC
    var now = new Date();
    var utcH = now.getUTCHours();
    var nextH = utcH < 8 ? 8 : utcH < 16 ? 16 : 24;
    var target = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), nextH, 0, 0));
    if (nextH === 24) target = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 0));
    return Math.max(0, target.getTime() - now.getTime());
  }
  function formatHMS(ms) {
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    function p(n) { return n < 10 ? '0' + n : '' + n; }
    return h + ':' + p(m) + ':' + p(sec);
  }
  function depthLadderOf(r) {
    // 优先用实时深度,回退到快照
    if (r._depth_ladder && (r._depth_ladder.bids || []).length) return r._depth_ladder;
    var sq = r.signal_quality || {};
    if (sq.depth_ladder && (sq.depth_ladder.bids || []).length) return sq.depth_ladder;
    return { bids: [], asks: [] };
  }
  function renderDepthDom() {
    var host = document.getElementById('depth-dom');
    if (!host) return;
    var r = cur();
    if (!r) { host.innerHTML = '<div class="dom-empty">暂无币种</div>'; return; }
    var ladder = depthLadderOf(r);
    var bids = ladder.bids || [];
    var asks = ladder.asks || [];
    if (!bids.length || !asks.length) {
      host.innerHTML = '<div class="dom-empty">盘口深度加载中…</div>';
      return;
    }
    // 归一化:用全档最大 usd 作为条形长度基准
    var maxUsd = 0;
    bids.forEach(function (b) { if (b[2] > maxUsd) maxUsd = b[2]; });
    asks.forEach(function (a) { if (a[2] > maxUsd) maxUsd = a[2]; });
    if (!maxUsd) maxUsd = 1;
    // 大单墙阈值:usd > 平均值的 2.5 倍
    var sumUsd = 0, n = 0;
    bids.forEach(function (b) { sumUsd += b[2]; n++; });
    asks.forEach(function (a) { sumUsd += a[2]; n++; });
    var avgUsd = n ? sumUsd / n : 0;
    var wallThreshold = avgUsd * 2.5;
    function isWall(usd) { return usd >= wallThreshold && usd > 0; }
    function barWidth(usd) { return Math.max(3, (usd / maxUsd) * 100); }
    function rowHtml(item, side) {
      var p = item[0], q = item[1], usd = item[2];
      var wall = isWall(usd) ? ' wall' : '';
      var w = barWidth(usd);
      return '<div class="dom-row ' + side + wall + '">' +
        '<div class="bar" style="width:' + w + '%"></div>' +
        '<span class="price">' + fmtPrice(p) + '</span>' +
        '<span class="qty">' + fmtVol(q) + '</span>' +
        '<span class="usd">' + fmtVol(usd) + 'U</span>' +
      '</div>';
    }
    // ask 倒序(最高价在上),bid 正序(最高价在上)
    var askHtml = asks.slice().reverse().map(function (a) { return rowHtml(a, 'ask'); }).join('');
    var bidHtml = bids.map(function (b) { return rowHtml(b, 'bid'); }).join('');
    // 中间区
    var mid = (bids[0][0] + asks[0][0]) / 2;
    var spread = asks[0][0] - bids[0][0];
    var spreadPct = mid ? (spread / mid * 100) : 0;
    var imbalance = r._depth_imbalance != null ? r._depth_imbalance : (r.signal_quality && r.signal_quality.depth_imbalance);
    var imbNum = Number(imbalance || 0);
    var imbCls = imbNum >= 0.15 ? 'bull' : imbNum <= -0.15 ? 'bear' : 'neutral';
    var imbPlain = imbNum >= 0.15 ? '多头垫厚' : imbNum <= -0.15 ? '空头垫厚' : '买卖均衡';
    host.innerHTML =
      '<div class="dom-side ask">' + askHtml + '</div>' +
      '<div class="dom-side mid">' +
        '<div class="dom-mid-spread">价差 ' + spreadPct.toFixed(3) + '%</div>' +
        '<div class="dom-mid-price">' + fmtPrice(mid) + '</div>' +
        '<div class="dom-mid-imbalance ' + imbCls + '">IMB ' + (imbNum >= 0 ? '+' : '') + imbNum.toFixed(2) + '</div>' +
        '<div class="dom-mid-plain">' + imbPlain + '</div>' +
      '</div>' +
      '<div class="dom-side bid">' + bidHtml + '</div>';
  }
  function mini(l, v) {
    return '<div class="mini"><div class="l">' + l + '</div><div class="v">' + v + '</div></div>';
  }

  /* ---------- heatmap ---------- */
  function chartHeatmap() {
    var c = init('chart-heatmap'); if (!c) return;
    var r = cur(); if (!r) return;
    var heat = [];
    TFS.forEach(function (tf, xi) {
      var ind = r.indicators[tf];
      var num = ind.emt === 'bull' ? 2 : ind.emt === 'bear' ? 0 : 1;
      heat.push({ value: [xi, 0, num], emt: ind.emt, rsi: ind.rsi14, tf: tf });
    });
    c.setOption({
      animation: false,
      tooltip: {
        appendToBody: true, backgroundColor: 'rgba(8,16,30,.92)', borderColor: C.rule, textStyle: { color: C.ink, fontFamily: FONT },
        formatter: function (p) {
          var d = p.data;
          return '<b>' + r.symbol + ' · ' + d.tf + '</b><br>趋势: ' + (d.emt === 'bull' ? '偏多' : d.emt === 'bear' ? '偏空' : '震荡') + '<br>RSI: ' + d.rsi.toFixed(1);
        }
      },
      grid: { left: 78, right: 24, top: 22, bottom: 28 },
      xAxis: { type: 'category', data: TFS, splitArea: { show: false }, axisLine: { lineStyle: { color: C.rule } }, axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 12 }, axisTick: { show: false } },
      yAxis: { type: 'category', data: [r.symbol], splitArea: { show: false }, axisLine: { lineStyle: { color: C.rule } }, axisLabel: { color: C.ink, fontFamily: FONT, fontSize: 13, fontWeight: 700 }, axisTick: { show: false } },
      visualMap: { show: false, min: 0, max: 2, inRange: { color: [C.bear, C.warn, C.bull] } },
      series: [{
        type: 'heatmap', data: heat,
        label: { show: true, color: '#04121f', fontWeight: 700, fontFamily: FONT, fontSize: 14, formatter: function (p) { return emtText(p.data.emt); } },
        itemStyle: { borderColor: C.bg2, borderWidth: 3, borderRadius: 6 },
        emphasis: { itemStyle: { shadowBlur: 12, shadowColor: 'rgba(0,0,0,.5)' } }
      }]
    });
  }

  /* ---------- grouped bar factory ---------- */
  function groupedBar(id, title, valFn, opts) {
    var c = init(id); if (!c) return;
    var r = cur(); if (!r) return;
    opts = opts || {};
    var vals = TFS.map(function (tf) { return valFn(r, tf); });
    var data;
    if (opts.perPointColor) {
      data = vals.map(function (v) {
        return { value: v, itemStyle: { color: opts.perPointColor(v), borderRadius: [4, 4, 0, 0] } };
      });
    } else {
      data = vals;
    }
    var series = [{
      name: r.symbol, type: 'bar', data: data,
      barWidth: '46%',
      itemStyle: { color: C.accent, borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', color: C.ink, fontFamily: FONT, fontSize: 10, formatter: function (p) { return Number(p.value).toFixed(opts.digits == null ? 2 : opts.digits) + (opts.unit || ''); } }
    }];
    var opt = {
      animation: false,
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true,
        backgroundColor: 'rgba(8,16,30,.92)', borderColor: C.rule, textStyle: { color: C.ink, fontFamily: FONT },
        valueFormatter: function (v) { return (v == null ? '-' : Number(v).toFixed(opts.digits == null ? 2 : opts.digits)) + (opts.unit || ''); }
      },
      grid: { left: 46, right: 20, top: 30, bottom: 26 },
      xAxis: { type: 'category', data: TFS, axisLine: { lineStyle: { color: C.rule } }, axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 11 }, axisTick: { show: false } },
      yAxis: {
        type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: C.rule, type: 'dashed' } },
        axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 10, formatter: opts.yFmt || null },
        min: opts.min, max: opts.max
      },
      series: series
    };
    if (opts.markLines) opt.series[0].markLine = { symbol: 'none', silent: true, lineStyle: { type: 'dashed' }, data: opts.markLines };
    if (opts.markAreas) opt.series[0].markArea = { silent: true, itemStyle: { color: 'rgba(255,59,92,.08)' }, data: opts.markAreas };
    c.setOption(opt, true);
  }

  function rsiLines() {
    return [
      { yAxis: 70, lineStyle: { color: C.bear }, label: { formatter: '超买 70', color: C.bear, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } },
      { yAxis: 50, lineStyle: { color: C.muted }, label: { formatter: '50', color: C.muted, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } },
      { yAxis: 30, lineStyle: { color: C.bull }, label: { formatter: '超卖 30', color: C.bull, fontFamily: FONT, fontSize: 9, position: 'insideEndBottom' } }
    ];
  }

  /* ---------- multi-timeframe change chart ---------- */
  function chartChange() {
    var c = init('chart-change'); if (!c) return;
    var r = cur(); if (!r) return;
    var tfs5 = ['15m', '1h', '4h', '8h', '24h'];
    var labels = ['15M', '1H', '4H', '8H', '24H'];
    var vals = tfs5.map(function (tf) {
      var v = (tf === '24h') ? r.pct_24h : r.indicators[tf].change_pct;
      return { value: v, itemStyle: { color: v >= 0 ? C.bull : C.bear, borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } };
    });
    var series = [{
      name: r.symbol, type: 'bar', data: vals, barWidth: '46%',
      label: {
        show: true, position: 'top', fontFamily: FONT, fontSize: 11, color: C.ink,
        formatter: function (p) { return (p.value >= 0 ? '+' : '') + Number(p.value).toFixed(2) + '%'; }
      }
    }];
    c.setOption({
      animation: false,
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true,
        backgroundColor: 'rgba(8,16,30,.92)', borderColor: C.rule, textStyle: { color: C.ink, fontFamily: FONT },
        valueFormatter: function (v) { return (v == null ? '-' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'); }
      },
      grid: { left: 50, right: 20, top: 30, bottom: 26 },
      xAxis: { type: 'category', data: labels, axisLine: { lineStyle: { color: C.rule } }, axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 12 }, axisTick: { show: false } },
      yAxis: {
        type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: C.rule, type: 'dashed' } },
        axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 10, formatter: function (v) { return v + '%'; } }
      },
      series: series
    }, true);
  }

  /* ---------- grid custom chart ---------- */
  function chartGrid(domId, r) {
    var c = init(domId); if (!c) return;
    var lg = r.long_grid, sg = r.short_grid, last = r.last;
    var bands = [
      [0, lg.lower, lg.upper, C.bull],
      [1, sg.lower, sg.upper, C.bear]
    ];
    var vals = [lg.lower, lg.entry, lg.upper, lg.stop, sg.lower, sg.entry, sg.upper, sg.stop, last];
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var pad = (hi - lo) * 0.14 || hi * 0.05;
    function lvl(y, color, label) {
      return { yAxis: y, lineStyle: { color: color, width: 1.4 }, label: { formatter: label, color: color, fontFamily: FONT, fontSize: 10, position: 'insideEndTop' } };
    }
    c.setOption({
      animation: false,
      title: { text: r.symbol, left: 8, top: 4, textStyle: { color: C.ink, fontFamily: "'Tektur','Microsoft YaHei',sans-serif", fontSize: 15, fontWeight: 500 } },
      tooltip: {
        appendToBody: true, backgroundColor: 'rgba(8,16,30,.92)', borderColor: C.rule, textStyle: { color: C.ink, fontFamily: FONT },
        formatter: function (p) { return r.symbol + '<br>价格: ' + fmtPrice(p.value); }
      },
      grid: { left: 78, right: 70, top: 36, bottom: 24 },
      xAxis: { type: 'category', data: ['做多网格', '做空网格'], axisLine: { lineStyle: { color: C.rule } }, axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 11 }, axisTick: { show: false } },
      yAxis: {
        type: 'value', min: lo - pad, max: hi + pad,
        axisLine: { show: false }, splitLine: { lineStyle: { color: C.rule, type: 'dashed' } },
        axisLabel: { color: C.muted, fontFamily: FONT, fontSize: 9, formatter: function (v) { return fmtPrice(v); } }
      },
      series: [
        {
          name: 'band', type: 'custom',
          renderItem: function (params, api) {
            var cat = api.value(0), ylo = api.value(1), yhi = api.value(2), col = api.value(3);
            var top = api.coord([cat, yhi]);
            var bot = api.coord([cat, ylo]);
            var halfW = api.size([0.62, 0])[0] / 2;
            return {
              type: 'group',
              children: [
                { type: 'rect', shape: { x: top[0] - halfW, y: top[1], width: halfW * 2, height: bot[1] - top[1] }, style: { fill: col, opacity: 0.16, stroke: col, lineWidth: 1.2 } },
                { type: 'circle', shape: { cx: top[0], cy: top[1], r: 3 }, style: { fill: col } },
                { type: 'circle', shape: { cx: top[0], cy: bot[1], r: 3 }, style: { fill: col } }
              ]
            };
          },
          data: bands,
          encode: { x: 0, y: [1, 2] },
          z: 2
        },
        {
          name: 'lvls', type: 'scatter', data: [[0, last], [1, last]], symbolSize: 0.001,
          markLine: {
            symbol: 'none', silent: true, animation: false,
            data: [
              lvl(lg.entry, C.bull, '1h多入场 ' + fmtPrice(lg.entry)),
              lvl(lg.stop, C.warn, '1h多止损 ' + fmtPrice(lg.stop)),
              lvl(sg.entry, C.bear, '1h空入场 ' + fmtPrice(sg.entry)),
              lvl(sg.stop, C.warn, '1h空止损 ' + fmtPrice(sg.stop)),
              { yAxis: last, lineStyle: { color: C.accent, width: 1.6, type: 'dashed' }, label: { formatter: '现价 ' + fmtPrice(last), color: C.accent, fontFamily: FONT, fontSize: 10, position: 'insideStartBottom' } }
            ]
          },
          z: 3
        }
      ]
    });
  }

  /* ---------- advice cards (current symbol only) ---------- */
  var currentHorizon = '超短线';
  function renderAdvice() {
    var grid = document.getElementById('advice-grid');
    if (!grid) return;
    var r = cur(); if (!r) { grid.innerHTML = ''; return; }
    // bind horizon buttons
    var btns = document.querySelectorAll('.hg-btn');
    for (var bi = 0; bi < btns.length; bi++) {
      if (btns[bi]._bound) continue;
      btns[bi]._bound = true;
      btns[bi].addEventListener('click', function () {
        currentHorizon = this.getAttribute('data-horizon');
        for (var j = 0; j < btns.length; j++) btns[j].classList.remove('active');
        this.classList.add('active');
        renderSignal();
        renderRisks();
        renderAccountRisk();
        renderSignalHistory();
        updateAdviceHighlight();
      });
    }
    var html = '';
    r.timeframe_advice.forEach(function (a) {
      var bc = biasClass(a.bias);
      var confColor = bc === 'bull' ? C.bull : bc === 'bear' ? C.bear : C.warn;
      var directionScore = directionScoreOf(a, r);
      var executionScore = executionScoreOf(a, r);
      var sizing = sizingOf(a, r);
      var pos = positionFromSizing(sizing, executionScore);
      var cardRisk = a.risk_gate === '禁止开仓'
        ? { key: 'block' }
        : a.risk_gate === '禁止半仓'
          ? { key: 'high' }
          : { key: 'low' };
      pos = riskAdjustedPosition(pos, cardRisk, { candleState: a.candle_state, trigger: a.trigger_check });
      if (!a._base_reasons) a._base_reasons = (a.reasons || []).slice();
      var extraReasons = [
        'K线状态：' + (a.candle_state || '-'),
        '风控阀门：' + gateText(a.risk_gate),
        '方向质量：' + directionScore + '/100',
        '开仓执行：' + executionScore + '/100' + (a.execution_note ? '，' + a.execution_note : ''),
        '历史回测：' + compactBacktest(a.backtest),
        '触发确认：' + triggerLabel(a.trigger_check),
        '风险预算：仓位' + (sizing ? fmtSizePct(sizing.suggested_size_pct) : '0%') +
          (sizing ? '，预算' + Number(sizing.risk_budget_pct || 0).toFixed(2) + '%，止损距离' + Number(sizing.stop_distance_pct || 0).toFixed(2) + '%' : '')
      ].concat(a._base_reasons || []);
      a.reasons = extraReasons;
      html +=
        '<div class="acard ' + bc + '" data-horizon="' + a.name + '">' +
          '<div class="glow"></div>' +
          '<h3>' + a.name + '</h3>' +
          '<div class="hor">' + a.horizon + '</div>' +
          '<div class="act">' + a.action + '</div>' +
          '<div class="lvl"><span class="k">偏向</span><span class="v ' + bc + '">' + biasPlain(a.bias) + '</span></div>' +
          '<div class="lvl"><span class="k">方向质量</span><span class="v">' + directionScore + '/100</span></div>' +
          '<div class="lvl"><span class="k">开仓执行</span><span class="v" style="color:' + confColor + '">' + executionScore + '/100</span></div>' +
          '<div class="lvl"><span class="k">建议仓位</span><span class="v" title="' + (pos.note || '') + '" style="color:' + pos.color + '">' + pos.text + '</span></div>' +
          '<div class="lvl"><span class="k">多入场</span><span class="v long">' + fmtPrice(a.long_entry) + '</span></div>' +
          '<div class="lvl"><span class="k">空入场</span><span class="v short">' + fmtPrice(a.short_entry) + '</span></div>' +
          '<div class="lvl"><span class="k">风控止损</span><span class="v stop">' + fmtPrice(a.stop_hint) + '</span></div>' +
          '<div class="confbar"><i style="width:' + executionScore + '%;background:' + confColor + '"></i></div>' +
          '<div class="reasons"><div class="rt">依据</div><ul>' + a.reasons.map(function (x) { return '<li>' + x + '</li>'; }).join('') + '</ul></div>' +
        '</div>';
    });
    grid.innerHTML = html;
    updateAdviceHighlight();
  }
  function updateAdviceHighlight() {
    var cards = document.querySelectorAll('#advice-grid .acard');
    for (var i = 0; i < cards.length; i++) {
      var name = cards[i].getAttribute('data-horizon') || '';
      if (name === currentHorizon || name.indexOf(currentHorizon) === 0) {
        cards[i].classList.add('highlighted'); cards[i].classList.remove('dimmed');
      } else {
        cards[i].classList.add('dimmed'); cards[i].classList.remove('highlighted');
      }
    }
  }

  function renderAccountRisk() {
    var host = document.getElementById('account-risk');
    if (!host) return;
    lastGuardRenderAt = Date.now();
    var oldManual = host.querySelector('.manual-fuse');
    var manualOpen = !!(oldManual && oldManual.open);
    var r = cur();
    var guard = openingGuardDecision(r);
    var fuse = accountFuseStatus();
    var state = fuse.state;
    var cls = fuse.active ? 'danger' : (state.dailyLossPct || state.consecutiveLosses || state.singleLossPct ? 'warn' : 'ok');
    var untilText = fuse.active && fuse.until ? ' · 恢复 ' + shortTime(fuse.until) : '';
    var reasonsHtml = guard.reasons.map(function (item) {
      return '<div class="guard-reason ' + (item.type || '') + '">' + htmlSafe(item.text) + '</div>';
    }).join('');
    var metricsHtml = guard.metrics.map(function (m) {
      return '<div class="guard-metric"><span>' + htmlSafe(m[0]) + '</span><b title="' + htmlSafe(m[1]) + '">' + htmlSafe(m[1]) + '</b></div>';
    }).join('');
    host.innerHTML =
      '<div class="guard-head">' +
        '<div><div class="guard-title">当前开仓结论</div><div class="guard-action">' + htmlSafe(guard.action) + '</div></div>' +
        '<div class="guard-pill ' + guard.key + '">' + htmlSafe(guard.label) + '</div>' +
      '</div>' +
      '<div class="guard-reasons">' + reasonsHtml + '</div>' +
      (metricsHtml ? '<div class="guard-metrics">' + metricsHtml + '</div>' : '') +
      '<details class="manual-fuse"' + (manualOpen ? ' open' : '') + '>' +
        '<summary>手动账户熔断备用（未接交易账户 API 时使用）</summary>' +
        '<div class="manual-fuse-body">' +
          '<div class="ar-status ' + cls + '">' + fuse.text + untilText + '</div>' +
          '<div class="ar-field"><label for="ar-daily-loss">今日亏损%</label><input id="ar-daily-loss" type="number" step="0.1" min="0" value="' + state.dailyLossPct + '"></div>' +
          '<div class="ar-field"><label for="ar-losses">连续亏损</label><input id="ar-losses" type="number" step="1" min="0" value="' + state.consecutiveLosses + '"></div>' +
          '<div class="ar-field"><label for="ar-single-loss">单笔亏损%</label><input id="ar-single-loss" type="number" step="0.1" min="0" value="' + state.singleLossPct + '"></div>' +
          '<div class="ar-actions"><button class="primary" id="ar-save">保存</button><button id="ar-reset">重置今日</button></div>' +
        '</div>' +
      '</details>';
    var saveBtn = document.getElementById('ar-save');
    var resetBtn = document.getElementById('ar-reset');
    function readInput(id) {
      var el = document.getElementById(id);
      var v = el ? Number(el.value) : 0;
      return isFinite(v) ? Math.max(0, v) : 0;
    }
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var next = loadAccountRiskState();
        next.date = todayKey();
        next.dailyLossPct = readInput('ar-daily-loss');
        next.consecutiveLosses = readInput('ar-losses');
        next.singleLossPct = readInput('ar-single-loss');
        next.fuseReasons = [];
        if (next.dailyLossPct >= 3 || next.consecutiveLosses >= 3 || next.singleLossPct >= 1.5) {
          next.fuseUntil = nextLocalMidnightMs();
        }
        saveAccountRiskState(next);
        render();
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        resetAccountRiskState();
        render();
      });
    }
  }
  function refreshOpeningGuardSoon() {
    var active = document.activeElement;
    if (active && /^ar-/.test(active.id || '')) return;
    if (Date.now() - lastGuardRenderAt < 2000) return;
    renderAccountRisk();
  }

  /* ---------- risks (current symbol only, plain language) ---------- */
  function renderRisks() {
    var host = document.getElementById('risk-grid'); if (!host) return;
    var r = cur(); if (!r) { host.innerHTML = ''; return; }
    var level = riskLevel(r);
    var html = '<div><div class="risk-summary"><span class="risk-pill ' + level.key + '">' + level.label + '</span><span>' + level.text + '</span></div><div class="risk-list">';
    r.risks.forEach(function (rk, i) {
      var danger = /过热|很高|很大|容易快速|过高|极端/.test(rk);
      // translate technical terms to plain language inline
      var plain = rk
        .replace(/RSI\s*[\d.]+/g, function(m){ return m + '(超买=涨太多)' })
        .replace(/BOLL\s*带宽\s*[\d.]+%/g, function(m){ return m + '(波动大)' })
        .replace(/ATR\s*[\d.]+%/g, function(m){ return m + '(波动率)' })
        .replace(/1m\/5m|短期.*?方向/g, function(m){ return m + '(短期方向乱)' });
      html += '<div class="risk' + (danger ? ' danger' : '') + '"><div class="ri">' + (i + 1) + '</div><div class="rt">' + plain + '</div></div>';
    });
    var exposure = correlationExposureForSymbol(r.symbol);
    if (exposure) {
      html += '<div class="risk danger"><div class="ri">!</div><div class="rt">' + exposure.text + '</div></div>';
    }
    var staleText = marketWarningText();
    if (staleText) {
      html += '<div class="risk"><div class="ri">D</div><div class="rt">数据可能延迟：' + staleText + '</div></div>';
    }
    html += '</div></div>';
    host.innerHTML = html;
  }

  /* ---------- render all panels (uses current DATA/GEN, single symbol view) ---------- */
  function renderEmptyState() {
    var msg = '<div class="empty">暂无币种，请在上方添加一个合约币种。</div>';
    ['signal-banner', 'kpi-main', 'indicator-lights', 'advice-grid', 'risk-grid', 'signal-history', 'signal-review', 'account-risk', 'depth-dom'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = msg;
    });
    ['chart-heatmap', 'chart-rsi', 'chart-boll', 'chart-atr', 'chart-ema', 'chart-vol', 'grid-main', 'chart-change'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = '';
    });
    renderTradingViewEmpty('No symbols loaded. Add a USDT futures symbol to show the TradingView K-Line.');
  }

  function render() {
    // destroy previous chart instances to avoid duplicate IDs on re-render
    charts.forEach(function (c) { c.dispose(); });
    charts.length = 0;
    setGen();
    renderSymbolTabs();
    var r = cur();
    if (!r || !DATA.length) { startRealtimePrices(); renderEmptyState(); return; }
    renderSignal();
    renderIndicatorLights();
    renderKpi();
    renderTradingViewKline();
    renderDepthDom();
    chartHeatmap();
    groupedBar('chart-rsi', 'RSI', function (r, tf) { return r.indicators[tf].rsi14; }, { digits: 1, min: 0, max: 100, markLines: rsiLines() });
    groupedBar('chart-boll', 'BOLL位置', function (r, tf) { return Math.max(0, Math.min(110, r.indicators[tf].boll_position_pct)); }, {
      digits: 0, unit: '%', min: 0, max: 110,
      markLines: [
        { yAxis: 90, lineStyle: { color: C.bear }, label: { formatter: '上轨90', color: C.bear, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } },
        { yAxis: 10, lineStyle: { color: C.bull }, label: { formatter: '下轨10', color: C.bull, fontFamily: FONT, fontSize: 9, position: 'insideEndBottom' } }
      ],
      markAreas: [[{ yAxis: 90 }, { yAxis: 110 }]]
    });
    groupedBar('chart-atr', 'ATR%', function (r, tf) { return r.indicators[tf].atr14_pct; }, {
      digits: 2, unit: '%',
      perPointColor: function (v) { return v >= 8 ? C.bear : v >= 3 ? C.warn : C.accent2; },
      markLines: [{ yAxis: 8, lineStyle: { color: C.bear }, label: { formatter: '高波 8%', color: C.bear, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } }]
    });
    groupedBar('chart-ema', 'EMA偏离', function (r, tf) { return r.indicators[tf].mt_pct_vs_ema20; }, {
      digits: 2, unit: '%',
      perPointColor: function (v) { return v >= 0 ? C.bull : C.bear; },
      markLines: [{ yAxis: 0, lineStyle: { color: C.muted }, label: { formatter: '0', color: C.muted, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } }]
    });
    groupedBar('chart-vol', '量能', function (r, tf) { return r.indicators[tf].vol_ratio_20; }, {
      digits: 2, unit: 'x',
      perPointColor: function (v) { return v >= 1.5 ? C.bull : v >= 0.8 ? C.accent : C.muted; },
      markLines: [{ yAxis: 1, lineStyle: { color: C.accent }, label: { formatter: '均值 1.0x', color: C.accent, fontFamily: FONT, fontSize: 9, position: 'insideEndTop' } }]
    });
    chartGrid('grid-main', r);
    chartChange();
    renderAdvice();
    renderRisks();
    renderAccountRisk();
    renderSignalHistory();
    renderSignalReview();
    loadSignalReviews(false);
    startRealtimePrices();
  }

  function onResize() { charts.forEach(function (c) { c.resize(); }); }

  function cleanup() {
    if (realtimeSource) {
      realtimeSource.close();
      realtimeSource = null;
      realtimeKey = '';
    }
    if (strategyRefreshTimer) {
      clearInterval(strategyRefreshTimer);
      strategyRefreshTimer = null;
    }
    if (clockTimer) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
    charts.forEach(function (c) { c.dispose(); });
    charts.length = 0;
    tvKlineKey = '';
    var tv = document.getElementById('tv-kline');
    if (tv) tv.innerHTML = '';
    window.removeEventListener('resize', onResize);
  }

  /* ---------- boot: fetch live data, then render ---------- */
  function showLoading(msg) {
    var el = document.getElementById('loading');
    if (!el) return;
    el.style.display = 'flex';
    var t = el.querySelector('.lt');
    if (t) t.textContent = msg;
  }
  function hideLoading() {
    var el = document.getElementById('loading');
    if (el) el.style.display = 'none';
  }

  function restoreCustomSymbols(done) {
    var custom = loadCustomSyms().filter(function (sym) {
      return !DATA.some(function (r) { return r.symbol === sym; });
    });
    if (!custom.length) { done(); return; }
    rememberSymbols(custom, true);
    showLoading('正在恢复 ' + custom.length + ' 个自定义币种…');
    fetch('api/market?symbols=' + encodeURIComponent(custom.join(',')), { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        var arr = stampReports(payload.data || [], payload.generated_at);
        arr.forEach(function (item) {
          if (!DATA.some(function (r) { return r.symbol === item.symbol; })) DATA.push(item);
        });
        GEN = payload.generated_at || GEN;
        updateLiveBadge(true, payload);
      })
      .catch(function () { /* keep defaults when custom restore fails */ })
      .then(done);
  }

  function boot() {
    tick();
    bindLogout();
    bindDiagnostics();
    bindPasswordChange();
    bindCreateUser();
    bindSignalReviewEvaluate();
    bindSignalReviewExport();
    if (!clockTimer) clockTimer = setInterval(tick, 1000);
    window.addEventListener('resize', onResize);
    window.addEventListener('beforeunload', cleanup);
    DATA = stampReports(DATA, GEN);
    showLoading('正在调用 bian.py 获取实时行情…');
    loadCurrentUser(function () {
      scopeLocalStorageKeys();
      loadServerPreferences(startMarketBoot);
    });

    function startMarketBoot() {
      seedSymbolHistoryFromLocal();
      // 1. load default symbols
    fetch('api/market', { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        DATA = stampReports(payload.data || DATA, payload.generated_at);
        applyRemovedSyms();
        GEN = payload.generated_at || GEN;
        LIVE = true;
        updateLiveBadge(true, payload);
        // 2. load saved custom symbols from localStorage in one batch
        restoreCustomSymbols(restoreDone);
      })
      .catch(function (err) {
        updateLiveBadge(false);
        console.warn('[dashboard] live fetch failed, using static data.js:', err.message);
        applyRemovedSyms();
        restoreCustomSymbols(restoreDone);
      });

    }

    function restoreDone() {
      applyRemovedSyms();
      renderSymbolTabs();
      recordSignalHistory('首次加载');
      render();
      hideLoading();
      startStrategyRefreshTimer();
    }
  }

  function updateLiveBadge(ok, payload) {
    var dot = document.querySelector('.live .dot');
    var live = document.querySelector('.live');
    var stale = !!(payload && payload.stale) || MARKET_STATE.stale;
    if (ok && stale) {
      if (dot) dot.style.background = 'var(--warn)';
      if (dot) dot.style.boxShadow = '0 0 12px var(--warn)';
      if (live) live.lastChild.textContent = ' 延迟';
    } else if (ok) {
      if (dot) dot.style.background = 'var(--accent2)';
      if (dot) dot.style.boxShadow = '0 0 12px var(--accent2)';
      if (live) live.lastChild.textContent = ' LIVE';
    } else {
      if (dot) dot.style.background = 'var(--warn)';
      if (dot) dot.style.boxShadow = '0 0 12px var(--warn)';
      if (live) live.lastChild.textContent = ' 离线';
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
