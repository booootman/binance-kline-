'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const chartsPath = path.join(root, 'web', 'assets', 'charts.js');
let source = fs.readFileSync(chartsPath, 'utf8');
const close = source.lastIndexOf('})();');
assert(close >= 0, 'charts.js closure marker missing');
source = source.slice(0, close) + `
  globalThis.__bianTest = {
    biasClass: biasClass,
    stampReports: stampReports,
    strategyFreshness: strategyFreshness,
    applyRealtimePrice: applyRealtimePrice,
    hasSymbolCapacity: hasSymbolCapacity,
    normalizeServerSymbolPreferences: normalizeServerSymbolPreferences,
    realtimeBadgeMode: realtimeBadgeMode,
    saveServerPreferences: saveServerPreferences,
    flushServerPreferences: flushServerPreferences,
    flushPreferencesOnUnload: flushPreferencesOnUnload,
    loadServerPreferences: loadServerPreferences,
    setData: function (items) { DATA = items || []; },
    getData: function () { return DATA; },
    setCurrentSymbol: function (value) { currentSymbol = value || ''; },
    setRealtimeState: function (value) {
      Object.keys(value || {}).forEach(function (key) { REALTIME_STATE[key] = value[key]; });
    },
    preferenceState: function () {
      return {
        pending: pendingPreferencePatch,
        inFlight: preferenceSaveInFlight,
        inFlightPatch: preferenceInFlightPatch
      };
    }
  };
` + source.slice(close);

const values = new Map();
const localStorage = {
  getItem(key) { return values.has(key) ? values.get(key) : null; },
  setItem(key, value) { values.set(key, String(value)); },
  removeItem(key) { values.delete(key); }
};

let timerId = 0;
const timers = new Map();
function setTimeoutMock(fn) {
  const id = ++timerId;
  timers.set(id, fn);
  return id;
}
function clearTimeoutMock(id) { timers.delete(id); }
function runTimers() {
  const pending = Array.from(timers.values());
  timers.clear();
  pending.forEach((fn) => fn());
}

const fetchCalls = [];
const fetchResolvers = [];
function fetchMock(url, options) {
  fetchCalls.push({ url, options: options || {} });
  return new Promise((resolve, reject) => fetchResolvers.push({ resolve, reject }));
}

const beacons = [];
let nowValue = 1_000;
const RealDate = Date;
class FakeDate extends RealDate {
  static now() { return nowValue; }
}

const window = {
  MARKET_DATA: [],
  GENERATED_AT: '',
  fetch: fetchMock,
  addEventListener() {},
  removeEventListener() {}
};
const document = {
  readyState: 'loading',
  documentElement: {},
  addEventListener() {},
  getElementById() { return null; },
  querySelector() { return null; },
  querySelectorAll() { return []; }
};
const context = {
  window,
  document,
  navigator: { sendBeacon(url, body) { beacons.push({ url, body }); return true; } },
  localStorage,
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  fetch: fetchMock,
  Blob,
  Date: FakeDate,
  console: { log: console.log, error: console.error, warn() {} },
  setTimeout: setTimeoutMock,
  clearTimeout: clearTimeoutMock,
  setInterval: setTimeoutMock,
  clearInterval: clearTimeoutMock,
  EventSource: function () {},
  URL,
  JSON,
  Math,
  Number,
  String,
  Object,
  Array,
  Promise,
  isFinite,
  encodeURIComponent,
  decodeURIComponent
};
context.globalThis = context;
window.window = window;
window.document = document;
window.localStorage = localStorage;
window.navigator = context.navigator;
window.EventSource = context.EventSource;

vm.createContext(context);
vm.runInContext(source, context, { filename: chartsPath });
const api = context.__bianTest;
assert(api, 'frontend test hooks were not installed');

function successfulResponse(call, overrideRevision) {
  const request = JSON.parse(call.options.body);
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve({ saved: true, applied: true, revision: overrideRevision || request.revision })
  };
}

function responsePayload(payload) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload)
  };
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  assert.strictEqual(api.biasClass('偏多'), 'bull', 'Chinese long bias must render as bull');
  assert.strictEqual(api.biasClass('偏空'), 'bear', 'Chinese short bias must render as bear');

  api.setData([]);
  const stamped = api.stampReports([{ symbol: 'DOGEUSDT' }], 'not-a-time');
  assert.strictEqual(stamped[0]._strategy_snapshot_ms, 0, 'invalid strategy time must fail closed');
  assert.strictEqual(api.strategyFreshness(stamped[0]).state, 'block', 'missing strategy time must block entry');

  api.setData(Array.from({ length: 8 }, (_, index) => ({ symbol: `S${index}USDT` })));
  assert.strictEqual(api.hasSymbolCapacity(), false, 'frontend must enforce the eight-symbol contract');

  api.setData([{ symbol: 'DOGEUSDT' }, { symbol: 'TLMUSDT' }]);
  const eightCustom = Array.from({ length: 8 }, (_, index) => `C${index}USDT`);
  const symbolPrefs = api.normalizeServerSymbolPreferences({
    removed_symbols: ['DOGEUSDT', 'TLMUSDT'],
    custom_symbols: eightCustom
  });
  assert.strictEqual(symbolPrefs.active.length, 0, 'removed defaults must not consume symbol capacity');
  assert.deepStrictEqual(Array.from(symbolPrefs.custom), eightCustom, 'all eight custom symbols must survive removed defaults');

  const reports = [{ symbol: 'DOGEUSDT' }, { symbol: 'TLMUSDT' }];
  api.setData(reports);
  api.setCurrentSymbol('TLMUSDT');
  nowValue = 1_000;
  api.applyRealtimePrice({ symbol: 'DOGEUSDT', price: 1, price_received_ms: 10, depth_received_ms: 20 });
  assert.strictEqual(reports[0]._price_snapshot_ms, 1_000, 'new server event must be stamped with browser receive time');
  nowValue = 5_000;
  api.applyRealtimePrice({ symbol: 'DOGEUSDT', price: 1, price_received_ms: 10, depth_received_ms: 20 });
  assert.strictEqual(reports[0]._price_snapshot_ms, 1_000, 'duplicate heartbeat must not keep price fresh');
  api.applyRealtimePrice({ symbol: 'DOGEUSDT', price: 1.1, price_received_ms: 11, depth_received_ms: 21 });
  assert.strictEqual(reports[0]._price_snapshot_ms, 5_000, 'new source event must refresh local freshness');

  nowValue = 10_000;
  api.saveServerPreferences({ custom_symbols: ['DOGEUSDT'] });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 1, 'first preference patch must start one request');
  api.saveServerPreferences({ custom_symbols: ['TLMUSDT'] });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 1, 'preference requests must be serialized');
  fetchResolvers[0].resolve(successfulResponse(fetchCalls[0]));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 2, 'newer patch must flush after the first request completes');
  const secondBody = JSON.parse(fetchCalls[1].options.body);
  assert.deepStrictEqual(secondBody.preferences.custom_symbols, ['TLMUSDT'], 'newer preference patch must win');
  fetchResolvers[1].resolve(successfulResponse(fetchCalls[1]));
  await settle();

  api.saveServerPreferences({ account_risk: { daily_loss_pct: 2 } });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 3, 'conflict test must start a preference request');
  const conflictRequest = JSON.parse(fetchCalls[2].options.body);
  fetchResolvers[2].resolve(responsePayload({ saved: true, applied: false, revision: conflictRequest.revision + 1 }));
  await settle();
  assert.strictEqual(fetchCalls.length, 4, 'preference conflict must fetch current server state');
  assert.strictEqual(fetchCalls[3].url, 'api/preferences', 'conflict recovery must use the preference endpoint');
  assert.strictEqual(fetchCalls[3].options.method, undefined, 'conflict recovery must read instead of replaying the stale patch');
  fetchResolvers[3].resolve(responsePayload({
    preferences: { account_risk: { daily_loss_pct: 1 } },
    revision: conflictRequest.revision + 1,
    storage: { mysql: { configured: true, available: true } }
  }));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 4, 'same-key conflict must not promote and retry the rejected value');
  assert.strictEqual(Object.keys(api.preferenceState().pending).length, 0, 'same-key conflict must discard the stale patch');

  api.saveServerPreferences({ tv_kline_interval: '60' });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 5, 'non-overlapping conflict test must start a preference request');
  const nonOverlapRequest = JSON.parse(fetchCalls[4].options.body);
  fetchResolvers[4].resolve(responsePayload({ saved: true, applied: false, revision: nonOverlapRequest.revision + 1 }));
  await settle();
  assert.strictEqual(fetchCalls.length, 6, 'non-overlapping conflict must fetch current server state');
  fetchResolvers[5].resolve(responsePayload({
    preferences: { account_risk: { daily_loss_pct: 1 } },
    revision: nonOverlapRequest.revision + 1,
    storage: { mysql: { configured: true, available: true } }
  }));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 7, 'field unchanged on the server must retry after reconciliation');
  const nonOverlapRetry = JSON.parse(fetchCalls[6].options.body);
  assert.strictEqual(nonOverlapRetry.preferences.tv_kline_interval, '60', 'safe conflict retry must preserve the local field');
  fetchResolvers[6].resolve(successfulResponse(fetchCalls[6]));
  await settle();

  api.saveServerPreferences({ position_state: { DOGEUSDT: 'long' } });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 8, 'storage failure test must start a preference request');
  fetchResolvers[7].resolve(responsePayload({ saved: false, applied: false, revision: 0 }));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 9, 'HTTP 200 with saved=false must retry as a storage outage');
  const storageRetry = JSON.parse(fetchCalls[8].options.body);
  assert.deepStrictEqual(storageRetry.preferences.position_state, { DOGEUSDT: 'long' }, 'storage retry must preserve the patch');
  fetchResolvers[8].resolve(successfulResponse(fetchCalls[8]));
  await settle();

  let preferenceLoadDone = false;
  api.loadServerPreferences(() => { preferenceLoadDone = true; });
  assert.strictEqual(fetchCalls.length, 10, 'storage outage test must start a preference read');
  fetchResolvers[9].resolve(responsePayload({
    preferences: {},
    revision: 0,
    storage: { mysql: { configured: true, available: false } }
  }));
  await settle();
  runTimers();
  assert.strictEqual(preferenceLoadDone, true, 'preference load callback must complete during storage outage');
  assert.strictEqual(fetchCalls.length, 10, 'storage outage must not queue a full local snapshot writeback');

  api.setData([{ symbol: 'DOGEUSDT' }]);
  api.setCurrentSymbol('DOGEUSDT');
  api.setRealtimeState({ transportOpen: true, upstreamConnected: false, lastPayloadAt: nowValue, error: 'TimeoutError' });
  assert.strictEqual(api.realtimeBadgeMode(), 'offline', 'explicit upstream error without a fresh price must be offline');
  api.setRealtimeState({ transportOpen: true, upstreamConnected: false, lastPayloadAt: 0, error: '' });
  assert.strictEqual(api.realtimeBadgeMode(), 'connecting', 'initial SSE handshake without an upstream error may show connecting');

  api.saveServerPreferences({ removed_symbols: ['DOGEUSDT'] });
  api.flushServerPreferences();
  const unloadRequest = JSON.parse(fetchCalls[10].options.body);
  api.flushPreferencesOnUnload();
  assert.strictEqual(beacons.length, 1, 'pending unload preferences must use sendBeacon');
  const beaconBody = JSON.parse(await beacons[0].body.text());
  assert.deepStrictEqual(beaconBody.preferences.removed_symbols, ['DOGEUSDT'], 'beacon must carry the latest pending patch');
  assert.strictEqual(beaconBody.revision, unloadRequest.revision, 'in-flight unload replay must keep its original revision');
  fetchResolvers[10].resolve(successfulResponse(fetchCalls[10]));
  await settle();

  console.log('frontend smoke ok');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
