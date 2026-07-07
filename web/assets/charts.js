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
  var MARKET_STATE = { stale: false, warning: '', errorType: '' };
  var SIGNAL_HISTORY_KEY = 'bian_dashboard_signal_history';
  var POSITION_STATE_KEY = 'bian_dashboard_position_state';
  var ACCOUNT_RISK_KEY = 'bian_dashboard_account_risk';
  var TV_KLINE_INTERVAL_KEY = 'bian_dashboard_tv_kline_interval';
  var tvKlineInterval = loadTvKlineInterval();
  var tvKlineKey = '';
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
  function biasClass(b) {
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
    if (b.indexOf('观望偏空') >= 0) return '别追多，等高位做空';
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
      note: sizing.note || (allowed ? '风险预算仓位' : '禁止开仓')
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
      priceEl.textContent = priceMs ? relativeSnapshot(priceMs) : '等待实时价';
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
      return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
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
  function realtimePriceAge(r) {
    return r && r._price_snapshot_ms ? relativeSnapshot(r._price_snapshot_ms) : '等待实时';
  }
  function backtestSummary(bt) {
    if (!bt || !bt.windows || !bt.windows.length) return '暂无回测';
    var w = bt.windows[bt.windows.length - 1];
    if (!w || !w.sample_count) return '样本不足';
    var stop = w.stop_rate != null ? ' / 止损' + Number(w.stop_rate).toFixed(0) + '%' : '';
    var net = w.net_expectancy_pct != null ? ' / 净期望' + Number(w.net_expectancy_pct).toFixed(2) + '%' : '';
    var filtered = w.filtered_out_count ? ' / 过滤' + w.filtered_out_count : '';
    return '1H胜率' + Number(w.hit_rate).toFixed(0) + '% / 盈撤比' + Number(w.profit_drawdown_ratio).toFixed(2) + stop + net + filtered + ' / ' + (bt.grade || '-');
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
    return s.indexOf('偏空') >= 0 || s.indexOf('观望偏空') >= 0;
  }
  function isLongBias(b) {
    return String(b || '').indexOf('偏多') >= 0;
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
    if (conf < 40 || dangerCount >= 3 || atr >= 12) return { key: 'block', label: '禁止开仓', text: '信号质量差或波动过大，先别开新仓。' };
    if (conf < 55 || dangerCount >= 2 || risks.length >= 4 || atr >= 8) return { key: 'high', label: '高风险', text: '只能轻仓或等待更干净的位置。' };
    var exposure = correlationExposureForSymbol(r && r.symbol);
    if (exposure) return { key: 'mid', label: '相关性风险', text: exposure.text };
    if (conf < 70 || dangerCount >= 1 || risks.length >= 2 || atr >= 4) return { key: 'mid', label: '中风险', text: '允许观察，仓位需要控制。' };
    return { key: 'low', label: '低风险', text: '风险相对可控，但仍需止损。' };
  }
  function isRealtimePrejudgeSnap(snap) {
    if (!snap) return false;
    if (snap.candleState === '实时预判') return true;
    var t = snap.trigger || {};
    return String(t.label || '').indexOf('实时预判') >= 0 || (t.reasons || []).some(function (x) { return String(x).indexOf('未收盘') >= 0 || String(x).indexOf('实时预判') >= 0; });
  }
  function riskAdjustedPosition(pos, risk, snap) {
    if (!pos || !risk) return pos;
    if (accountFuseStatus().active) return { stars: '', text: '0%', sizePct: 0, color: C.bear, note: '账户熔断禁止开仓' };
    if (isRealtimePrejudgeSnap(snap) && snap.trigger && /confirmed|watch/.test(String(snap.trigger.status || ''))) {
      return { stars: '', text: '0%', sizePct: 0, color: C.warn, note: '等待收盘确认' };
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
    return '距现价 ' + (d >= 0 ? '+' : '') + d.toFixed(2) + '%';
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
    var state = getPositionState(r.symbol);
    if (stopDist != null && stopDist <= 0.4) {
      return { cls: 'danger', text: '接近风控止损，距离约 ' + stopDist.toFixed(2) + '%，优先处理风险。' };
    }
    if (state === '多单' && snap.side === 'short') {
      return { cls: 'danger', text: '你标记为多单，但当前信号偏空，优先看止损或减仓。' };
    }
    if (state === '空单' && snap.side === 'long') {
      return { cls: 'danger', text: '你标记为空单，但当前信号偏多，优先看止损或减仓。' };
    }
    if (isRealtimePrejudgeSnap(snap) && snap.trigger && /confirmed|watch/.test(String(snap.trigger.status || ''))) {
      return { cls: 'warn', text: '当前 K 线未收盘，只能观察；等 1m/对应周期收盘确认后再考虑入场。' };
    }
    if (snap.trigger && snap.trigger.status === 'blocked') {
      return { cls: 'danger', text: '入场位附近但触发确认不通过：' + (snap.trigger.reasons || []).join('；') };
    }
    if (snap.trigger && snap.trigger.status === 'confirmed') {
      return { cls: 'watch', text: '入场触发已确认：量能、价差和1m结构通过，仍需按仓位和止损执行。' };
    }
    if (snap.trigger && snap.trigger.status === 'watch') {
      return { cls: 'warn', text: '价格到位但还要等确认：' + (snap.trigger.reasons || []).join('；') };
    }
    if (entryDist != null && entryDist <= 0.25) {
      return { cls: 'watch', text: '已接近' + snap.entryLabel + '，距离约 ' + entryDist.toFixed(2) + '%，等待确认再动手。' };
    }
    if (entryDist != null && entryDist <= 0.7) {
      return { cls: 'warn', text: '价格在' + snap.entryLabel + '附近，距离约 ' + entryDist.toFixed(2) + '%，别追，等触发。' };
    }
    if (snap.side === 'short' && entrySigned != null && entrySigned > 1) {
      return { cls: 'warn', text: '现价低于反弹空点 ' + entrySigned.toFixed(2) + '%，这里不是追空位置，等反弹触发或看更短周期。' };
    }
    if (snap.side === 'long' && entrySigned != null && entrySigned < -1) {
      return { cls: 'warn', text: '现价高于回踩多点 ' + Math.abs(entrySigned).toFixed(2) + '%，这里不是追多位置，等回踩确认。' };
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
      return { cls: 'warn', text: '当前信号来自未收盘 K 线，仅作实时预判，等待收盘确认。' };
    }
    return null;
  }
  function loadPositionStates() {
    try { var v = localStorage.getItem(POSITION_STATE_KEY); return v ? JSON.parse(v) : {}; } catch (e) { return {}; }
  }
  function savePositionStates(map) {
    try { localStorage.setItem(POSITION_STATE_KEY, JSON.stringify(map || {})); } catch (e) {}
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
  function loadSignalHistory() {
    try {
      var v = localStorage.getItem(SIGNAL_HISTORY_KEY);
      var arr = v ? JSON.parse(v) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  function saveSignalHistory(arr) {
    try { localStorage.setItem(SIGNAL_HISTORY_KEY, JSON.stringify((arr || []).slice(0, 80))); } catch (e) {}
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
  function updateRealtimeSignal() {
    var r = cur();
    var snap = signalSnapshot(r);
    if (!r || !snap) return;
    var risk = riskLevel(r);
    var pos = riskAdjustedPosition(snap.position, risk, snap);
    var bestAdvice = snap.advice;
    var state = getPositionState(r.symbol);
    var livePriceEl = document.querySelector('#signal-banner .js-signal-live-price');
    var liveAgeEl = document.querySelector('#signal-banner .js-signal-live-age');
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
      sbEl.className = 'sb risk-' + risk.key + (bestAdvice && bestAdvice.candle_state === '实时预判' ? ' realtime-prejudge' : '');
    }
    if (livePriceEl) livePriceEl.textContent = realtimePriceText(r);
    if (liveAgeEl) {
      liveAgeEl.textContent = realtimePriceAge(r);
      liveAgeEl.title = r._price_snapshot_ms ? 'WebSocket 实时价格' : '等待 WebSocket 实时价格';
    }
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
    }
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
      // EventSource reconnects automatically; keep the last full-analysis state visible.
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
      renderDepthDom();
      setGen();
      updateLiveBadge(true);
    }
  }
  function refreshStrategyAnalysis() {
    if (strategyRefreshBusy || !DATA.length) return;
    var symbols = DATA.map(function (r) { return r.symbol; }).join(',');
    if (!symbols) return;
    strategyRefreshBusy = true;
    nextStrategyRefreshAt = Date.now();
    setGen();
    fetch('api/market?symbols=' + encodeURIComponent(symbols), { cache: 'no-store' })
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (payload) {
        applyMarketPayloadState(payload);
        var fresh = stampReports(payload.data || [], payload.generated_at);
        if (!fresh.length) throw new Error('empty data');
        DATA = fresh;
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
  var LS_KEY = 'bian_dashboard_custom_syms';
  var REMOVED_KEY = 'bian_dashboard_removed_syms';

  // localStorage: save / load / remove custom symbols
  function saveCustomSyms() {
    var custom = DATA.map(function (r) { return r.symbol; }).filter(function (s) { return DEFAULT_SYMS.indexOf(s) < 0; });
    try { localStorage.setItem(LS_KEY, JSON.stringify(custom)); } catch (e) {}
  }
  function loadCustomSyms() {
    try { var v = localStorage.getItem(LS_KEY); return v ? JSON.parse(v) : []; } catch (e) { return []; }
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
    var removed = loadRemovedSyms();
    if (DEFAULT_SYMS.indexOf(sym) >= 0 && removed.indexOf(sym) < 0) {
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
    if (!btn || !input || btn._bound) return;
    btn._bound = true;
    function doAdd() {
      var raw = (input.value || '').trim().toUpperCase();
      if (!raw) { setAddStatus('请输入币种', 'err'); return; }
      if (!raw.endsWith('USDT')) raw = raw + 'USDT';
      if (DATA.some(function (r) { return r.symbol === raw; })) {
        setAddStatus(raw + ' 已存在', 'err');
        return;
      }
      var btn2 = document.getElementById('add-sym-btn');
      btn2.disabled = true;
      setAddStatus('正在获取 ' + raw + ' 实时数据…', '');
      showLoading('正在获取 ' + raw + ' …');
      fetch('api/market?symbol=' + encodeURIComponent(raw), { cache: 'no-store' })
        .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
        .then(function (payload) {
          applyMarketPayloadState(payload);
          var arr = stampReports(payload.data || [], payload.generated_at);
          if (!arr.length) throw new Error('未取到数据');
          DATA = DATA.concat(arr);
          GEN = payload.generated_at || GEN;
          updateLiveBadge(true, payload);
          forgetRemovedSym(arr[0].symbol);
          currentSymbol = arr[0].symbol;
          saveCustomSyms();
          recordSignalHistory('添加币种');
          renderSymbolTabs();
          render();
          setAddStatus(raw + ' 添加成功 ✓', 'ok');
          input.value = '';
        })
        .catch(function (err) {
          setAddStatus('添加失败: ' + err.message, 'err');
        })
        .then(function () {
          btn2.disabled = false;
          hideLoading();
        });
    }
    btn.addEventListener('click', doAdd);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') doAdd(); });
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
    else if (bestAdvice && bestAdvice.candle_state === '实时预判') lightClass = 'yellow';
    var pos = riskAdjustedPosition(snap.position, risk, snap);
    var alert = signalAlert(r, snap);
    var state = getPositionState(r.symbol);
    var topLine = signalTopline(risk, bestAdvice, snap, state);
    var title = r.symbol.replace('USDT', '') + ' ' + (bestAdvice ? bestAdvice.name : '当前') + '：' + biasPlain(signalBias);
    var reason = bestAdvice ? bestAdvice.action : (r.summary || '');
    // 主行 5 核心:实时价 / 方向 / 开仓分 / 触发状态 / 建议仓位
    var majorHtml = '<div class="sb-act major">实时价<b class="js-signal-live-price" style="color:' + C.accent + '">' + realtimePriceText(r) + '</b><span class="minor js-signal-live-age" title="' + (r._price_snapshot_ms ? 'WebSocket 实时价格' : '等待 WebSocket 实时价格') + '">' + realtimePriceAge(r) + '</span></div>';
    if (bestAdvice) {
      var entryColor = snap.side === 'short' ? C.bear : snap.side === 'long' ? C.bull : C.accent;
      majorHtml += '<div class="sb-act major">方向分<b style="color:' + C.accent + '">' + Math.round(snap.directionScore || 0) + '</b></div>';
      majorHtml += '<div class="sb-act major">开仓分<b style="color:' + (snap.executionScore >= 60 ? C.accent2 : C.warn) + '">' + Math.round(snap.executionScore || 0) + '</b></div>';
      majorHtml += '<div class="sb-act major">触发<b style="color:' + (bestAdvice.trigger_check && bestAdvice.trigger_check.status === 'confirmed' ? C.accent2 : C.warn) + '">' + triggerLabel(bestAdvice.trigger_check) + '</b></div>';
      majorHtml += '<div class="sb-act major">建议仓位<b class="js-signal-position" title="' + (pos.note || '') + '" style="color:' + pos.color + '">' + pos.text + '</b></div>';
      // 次行辅助:K线/风控/回测/盘口/入场/止损/周期
      var minorHtml = '<div class="sb-act minor">K线<b style="color:' + (bestAdvice.candle_state === '收盘确认' ? C.accent2 : C.warn) + '">' + (bestAdvice.candle_state || '-') + '</b></div>';
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
    var sbClass = 'sb risk-' + risk.key + (bestAdvice && bestAdvice.candle_state === '实时预判' ? ' realtime-prejudge' : '');
    host.innerHTML =
      '<div class="' + sbClass + '">' +
        '<div class="light ' + lightClass + '"></div>' +
        '<div class="sb-body">' +
          '<div class="sb-topline js-signal-topline' + (topLine ? ' show ' + topLine.cls : '') + '">' + (topLine ? topLine.text : '') + '</div>' +
          '<div class="sb-title">' + title + '</div>' +
          '<div class="sb-reason">' + reason + '</div>' +
          '<div class="sb-actions">' + majorHtml + '</div>' +
          (minorHtml ? '<div class="sb-actions-minor">' + minorHtml + '</div>' : '') +
          '<div class="sb-meta"><span class="risk-pill js-risk-level ' + risk.key + '" title="' + risk.text + '">' + risk.label + '</span>' + stateHtml + '</div>' +
          '<div class="sb-alert js-signal-alert' + (alert.text ? ' show ' + alert.cls : '') + '">' + alert.text + '</div>' +
        '</div>' +
      '</div>';
    bindPositionStateButtons(host);
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
    var fuse = accountFuseStatus();
    var state = fuse.state;
    var cls = fuse.active ? 'danger' : (state.dailyLossPct || state.consecutiveLosses || state.singleLossPct ? 'warn' : 'ok');
    var untilText = fuse.active && fuse.until ? ' · 恢复 ' + shortTime(fuse.until) : '';
    host.innerHTML =
      '<div class="ar-status ' + cls + '">' + fuse.text + untilText + '</div>' +
      '<div class="ar-field"><label for="ar-daily-loss">今日亏损%</label><input id="ar-daily-loss" type="number" step="0.1" min="0" value="' + state.dailyLossPct + '"></div>' +
      '<div class="ar-field"><label for="ar-losses">连续亏损</label><input id="ar-losses" type="number" step="1" min="0" value="' + state.consecutiveLosses + '"></div>' +
      '<div class="ar-field"><label for="ar-single-loss">单笔亏损%</label><input id="ar-single-loss" type="number" step="0.1" min="0" value="' + state.singleLossPct + '"></div>' +
      '<div class="ar-actions"><button class="primary" id="ar-save">保存</button><button id="ar-reset">重置今日</button></div>';
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
    ['signal-banner', 'kpi-main', 'indicator-lights', 'advice-grid', 'risk-grid', 'signal-history', 'account-risk', 'depth-dom'].forEach(function (id) {
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
    if (!clockTimer) clockTimer = setInterval(tick, 1000);
    window.addEventListener('resize', onResize);
    window.addEventListener('beforeunload', cleanup);
    DATA = stampReports(DATA, GEN);
    showLoading('正在调用 bian.py 获取实时行情…');
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

    function restoreDone() {
      applyRemovedSyms();
      renderSymbolTabs();
      recordSignalHistory('首次加载');
      render();
      hideLoading();
      startStrategyRefreshTimer();
      console.log('[dashboard] live data + custom symbols loaded @', GEN);
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
