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
  var SIGNAL_HISTORY_KEY = 'bian_dashboard_signal_history';
  var POSITION_STATE_KEY = 'bian_dashboard_position_state';
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
  function emtText(e) { return e === 'bull' ? '多' : e === 'bear' ? '空' : '震'; }
  function emtColor(e) { return e === 'bull' ? C.bull : e === 'bear' ? C.bear : C.warn; }
  function init(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    var c = echarts.init(el, null, { renderer: 'svg' });
    charts.push(c);
    return c;
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
  function formatCountdown(targetMs) {
    if (!targetMs) return '未启动';
    var left = Math.max(0, Math.ceil((targetMs - Date.now()) / 1000));
    if (!left) return strategyRefreshBusy ? '刷新中' : '即将刷新';
    var m = Math.floor(left / 60);
    var s = left % 60;
    return m + '分' + (s < 10 ? '0' : '') + s + '秒';
  }
  function stampReports(arr, generatedAt) {
    var stamp = generatedAt || GEN || '';
    var ms = parseSnapshotMs(stamp) || Date.now();
    return (arr || []).map(function (r) {
      if (!r || typeof r !== 'object') return r;
      r._snapshot_at = stamp;
      r._snapshot_ms = ms;
      r._strategy_snapshot_at = stamp;
      r._strategy_snapshot_ms = ms;
      r._analysis_last = Number(r.last) || 0;
      r._analysis_confidence = Number(r.confidence) || 0;
      (r.timeframe_advice || []).forEach(function (a) {
        a._base_long_entry = Number(a.long_entry);
        a._base_short_entry = Number(a.short_entry);
        a._base_stop_hint = Number(a.stop_hint);
      });
      return r;
    });
  }
  function bestSignalAdvice(r) {
    if (!r || !r.timeframe_advice || !r.timeframe_advice.length) return null;
    return r.timeframe_advice.reduce(function (a, b) { return a.confidence > b.confidence ? a : b; });
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
    var base = advice && advice.confidence != null
      ? Number(advice.confidence)
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
    var entry = advice ? adviceValue(advice, entryKey) : null;
    return {
      advice: advice,
      side: side,
      entryLabel: entryLabel(side),
      entryDistance: entry == null ? '' : entryDistanceText(r, entry),
      entry: entry,
      stop: advice ? adviceValue(advice, 'stop_hint') : null,
      position: confToPosition(confidence)
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
    var dangerCount = risks.filter(function (x) { return /过热|很高|很大|容易快速|过高|极端|追|扫掉|穿越/.test(x); }).length;
    var atr = selectedAtrPct(r, advice);
    var conf = realtimeConfidence(r, advice);
    if (advice && String(advice.bias || '').indexOf('观望') >= 0 && conf < 60) {
      return { key: 'high', label: '高风险', text: '当前周期方向不够干净，先等触发位或换更短确认。' };
    }
    if (conf < 40 || dangerCount >= 3 || atr >= 12) return { key: 'block', label: '禁止开仓', text: '信号质量差或波动过大，先别开新仓。' };
    if (conf < 55 || dangerCount >= 2 || risks.length >= 4 || atr >= 8) return { key: 'high', label: '高风险', text: '只能轻仓或等待更干净的位置。' };
    if (conf < 70 || dangerCount >= 1 || risks.length >= 2 || atr >= 4) return { key: 'mid', label: '中风险', text: '允许观察，仓位需要控制。' };
    return { key: 'low', label: '低风险', text: '风险相对可控，但仍需止损。' };
  }
  function riskAdjustedPosition(pos, risk) {
    if (!pos || !risk) return pos;
    if (risk.key === 'block') return { stars: '☆', text: '禁止开仓', color: C.bear };
    if (risk.key === 'high' && pos.text === '可半仓') return { stars: '★', text: '只可轻仓', color: C.warn };
    return pos;
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
    if (!r || !snap || snap.entry == null || snap.stop == null) return { cls: '', text: '' };
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
      r.confidence,
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
        confidence: r.confidence,
        advice: snap.advice ? snap.advice.name : '',
        entryLabel: snap.entryLabel,
        entryDistance: snap.entryDistance,
        entry: snap.entry,
        stop: snap.stop,
        position: pos ? (pos.stars + ' ' + pos.text) : '',
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
    if (!r) { host.innerHTML = '<div class="empty">暂无信号历史</div>'; return; }
    var items = loadSignalHistory().filter(function (x) { return x.symbol === r.symbol; }).slice(0, 8);
    if (!items.length) { host.innerHTML = '<div class="empty">暂无信号历史</div>'; return; }
    host.innerHTML = items.map(function (x) {
      return '<div class="history-item">' +
        '<div class="ht"><span>' + shortTime(x.ts) + '</span><span>' + x.reason + '</span></div>' +
        '<div class="hb"><b>' + x.bias + '</b> · ' + x.confidence + '/100 · ' + x.risk + '<br>' +
        (x.entryLabel || '入场') + ' ' + fmtPrice(Number(x.entry || 0)) + (x.entryDistance ? ' · ' + x.entryDistance : '') + ' · 止损 ' + fmtPrice(Number(x.stop || 0)) + '<br>' +
        x.position + ' · ' + x.advice + '</div>' +
      '</div>';
    }).join('');
  }
  function updateRealtimeSignal() {
    var r = cur();
    var snap = signalSnapshot(r);
    if (!r || !snap) return;
    var risk = riskLevel(r);
    var pos = riskAdjustedPosition(snap.position, risk);
    var livePriceEl = document.querySelector('#signal-banner .js-signal-live-price');
    var liveAgeEl = document.querySelector('#signal-banner .js-signal-live-age');
    var entryLabelEl = document.querySelector('#signal-banner .js-signal-entry-label');
    var entryEl = document.querySelector('#signal-banner .js-signal-entry');
    var distanceEl = document.querySelector('#signal-banner .js-signal-distance');
    var stopEl = document.querySelector('#signal-banner .js-signal-stop');
    var posEl = document.querySelector('#signal-banner .js-signal-position');
    var riskEl = document.querySelector('#signal-banner .js-risk-level');
    var alertEl = document.querySelector('#signal-banner .js-signal-alert');
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
      posEl.textContent = pos.stars + ' ' + pos.text;
      posEl.style.color = pos.color;
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
      r._price_bid = item.bid;
      r._price_ask = item.ask;
    });
    if (cur() && cur().symbol === item.symbol) {
      var el = document.querySelector('#kpi-main .price');
      if (el) {
        el.textContent = fmtPrice(price);
        el.title = '实时价格';
      }
      updateRealtimeSignal();
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
        var fresh = stampReports(payload.data || [], payload.generated_at);
        if (!fresh.length) throw new Error('empty data');
        DATA = fresh;
        applyRemovedSyms();
        GEN = payload.generated_at || GEN;
        if (!DATA.some(function (r) { return r.symbol === currentSymbol; })) {
          currentSymbol = DATA.length ? DATA[0].symbol : '';
        }
        LIVE = true;
        updateLiveBadge(true);
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
        var arr = stampReports(payload.data || [], payload.generated_at);
        if (!arr.length) throw new Error('未取到数据');
        // replace existing report for this symbol
        DATA = DATA.map(function (r) { return r.symbol === sym ? arr[0] : r; });
        GEN = payload.generated_at || GEN;
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
          var arr = stampReports(payload.data || [], payload.generated_at);
          if (!arr.length) throw new Error('未取到数据');
          DATA = DATA.concat(arr);
          GEN = payload.generated_at || GEN;
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
    var pos = riskAdjustedPosition(snap.position, risk);
    var alert = signalAlert(r, snap);
    var state = getPositionState(r.symbol);
    var title = r.symbol.replace('USDT', '') + ' ' + (bestAdvice ? bestAdvice.name : '当前') + '：' + biasPlain(signalBias);
    var reason = bestAdvice ? bestAdvice.action : (r.summary || '');
    var actionsHtml = '<div class="sb-act">实时价<b class="js-signal-live-price" style="color:' + C.accent + '">' + realtimePriceText(r) + '</b><span class="minor js-signal-live-age" title="' + (r._price_snapshot_ms ? 'WebSocket 实时价格' : '等待 WebSocket 实时价格') + '">' + realtimePriceAge(r) + '</span></div>';
    if (bestAdvice) {
      var entryColor = snap.side === 'short' ? C.bear : snap.side === 'long' ? C.bull : C.accent;
      actionsHtml += '<div class="sb-act"><span class="js-signal-entry-label">' + snap.entryLabel + '</span><b class="js-signal-entry" style="color:' + entryColor + '">' + fmtPrice(snap.entry) + '</b><span class="minor js-signal-distance">' + snap.entryDistance + '</span></div>';
      actionsHtml += '<div class="sb-act">风控止损<b class="js-signal-stop" style="color:' + C.warn + '">' + fmtPrice(snap.stop) + '</b></div>';
      actionsHtml += '<div class="sb-act">信号周期<b>' + bestAdvice.name + '</b></div>';
    }
    actionsHtml += '<div class="sb-act">建议仓位<b class="js-signal-position" style="color:' + pos.color + '">' + pos.stars + ' ' + pos.text + '</b></div>';
    var states = ['空仓', '多单', '空单'];
    var stateHtml = '<div class="pos-state" data-symbol="' + r.symbol + '"><span class="ps-label">当前状态</span>' +
      states.map(function (s) { return '<button class="pos-btn ' + (state === s ? 'active' : '') + '" data-state="' + s + '">' + s + '</button>'; }).join('') +
      '</div>';
    host.innerHTML =
      '<div class="sb">' +
        '<div class="light ' + lightClass + '"></div>' +
        '<div class="sb-body">' +
          '<div class="sb-title">' + title + '</div>' +
          '<div class="sb-reason">' + reason + '</div>' +
          '<div class="sb-actions">' + actionsHtml + '</div>' +
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
        mini('资金费率', (r.funding_rate * 100).toFixed(4) + '%') +
      '</div>' +
      '<div class="conf-wrap">' +
        '<div id="gauge-main"></div>' +
        '<div class="conf-meta">' +
          '<div class="cl">CONFIDENCE</div>' +
          '<div class="cv" style="color:' + confColor + '">' + r.confidence + '<span style="font-size:14px;color:' + C.muted + '">/100</span></div>' +
          '<div class="cb">综合置信度</div>' +
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
          data: [{ value: r.confidence }]
        }]
      });
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
      var pos = confToPosition(a.confidence);
      html +=
        '<div class="acard ' + bc + '" data-horizon="' + a.name + '">' +
          '<div class="glow"></div>' +
          '<h3>' + a.name + '</h3>' +
          '<div class="hor">' + a.horizon + '</div>' +
          '<div class="act">' + a.action + '</div>' +
          '<div class="lvl"><span class="k">偏向</span><span class="v ' + bc + '">' + biasPlain(a.bias) + '</span></div>' +
          '<div class="lvl"><span class="k">建议仓位</span><span class="v" style="color:' + pos.color + '">' + pos.stars + ' ' + pos.text + '</span></div>' +
          '<div class="lvl"><span class="k">多入场</span><span class="v long">' + fmtPrice(a.long_entry) + '</span></div>' +
          '<div class="lvl"><span class="k">空入场</span><span class="v short">' + fmtPrice(a.short_entry) + '</span></div>' +
          '<div class="lvl"><span class="k">风控止损</span><span class="v stop">' + fmtPrice(a.stop_hint) + '</span></div>' +
          '<div class="confbar"><i style="width:' + a.confidence + '%;background:' + confColor + '"></i></div>' +
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
    html += '</div></div>';
    host.innerHTML = html;
  }

  /* ---------- render all panels (uses current DATA/GEN, single symbol view) ---------- */
  function renderEmptyState() {
    var msg = '<div class="empty">暂无币种，请在上方添加一个合约币种。</div>';
    ['signal-banner', 'kpi-main', 'indicator-lights', 'advice-grid', 'risk-grid', 'signal-history'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = msg;
    });
    ['chart-heatmap', 'chart-rsi', 'chart-boll', 'chart-atr', 'chart-ema', 'chart-vol', 'grid-main', 'chart-change'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = '';
    });
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
        var arr = stampReports(payload.data || [], payload.generated_at);
        arr.forEach(function (item) {
          if (!DATA.some(function (r) { return r.symbol === item.symbol; })) DATA.push(item);
        });
        GEN = payload.generated_at || GEN;
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
        DATA = stampReports(payload.data || DATA, payload.generated_at);
        applyRemovedSyms();
        GEN = payload.generated_at || GEN;
        LIVE = true;
        updateLiveBadge(true);
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

  function updateLiveBadge(ok) {
    var dot = document.querySelector('.live .dot');
    var live = document.querySelector('.live');
    if (ok) {
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
