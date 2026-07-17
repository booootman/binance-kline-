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
    positionFromSizing: positionFromSizing,
    addSymbol: addSymbol,
    pendingSymbolAddCount: function () { return Object.keys(pendingSymbolAdds).length; },
    normalizeServerSymbolPreferences: normalizeServerSymbolPreferences,
    realtimeBadgeMode: realtimeBadgeMode,
    saveServerPreferences: saveServerPreferences,
    flushServerPreferences: flushServerPreferences,
    flushPreferencesOnUnload: flushPreferencesOnUnload,
    loadServerPreferences: loadServerPreferences,
    loadCurrentUser: loadCurrentUser,
    clearConflictRecoveryMemory: function () { preferenceConflictRecovery = null; },
    reloadConflictRecoveryMemory: function () { preferenceConflictRecovery = loadPreferenceConflictRecovery(); },
    setConflictRecovery: function (value) { rememberPreferenceConflictRecovery(value); },
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
        inFlightPatch: preferenceInFlightPatch,
        conflictRecovery: preferenceConflictRecovery,
        syncEnabled: preferenceServerSyncEnabled,
        currentUser: CURRENT_USER
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
  assert.strictEqual(api.positionFromSizing({
    suggested_size_pct: 30,
    max_size_pct: 30,
    risk_budget_pct: 0.5,
    stop_distance_pct: 1,
    allowed: true
  }, 67).sizePct, 22, 'realtime score downgrade must apply the current sizing tier');
  assert.strictEqual(api.positionFromSizing({ suggested_size_pct: 8, max_size_pct: 8, stop_distance_pct: 1, allowed: true }, 44).sizePct, 0, 'sub-threshold realtime score must remove the opening size');

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
  api.applyRealtimePrice({ symbol: 'DOGEUSDT', price: 0.5, price_received_ms: 9, depth_received_ms: 19 });
  assert.strictEqual(reports[0].last, 1.1, 'out-of-order realtime price must not overwrite the latest value');
  assert.strictEqual(reports[0]._price_source_ms, 11, 'out-of-order realtime price must not roll back source time');
  api.applyRealtimePrice({ symbol: 'DOGEUSDT', price: 0.4, price_event_ms: 10, price_received_ms: 99, depth_event_ms: 20, depth_received_ms: 99 });
  assert.strictEqual(reports[0].last, 1.1, 'older exchange event must not win because it arrived later');

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
  assert(api.preferenceState().conflictRecovery, 'conflict patch must persist before reconciliation GET completes');
  assert(Array.from(values.keys()).some((key) => key.includes('preference_recovery')), 'pagehide during reconciliation must retain recovery state');
  api.flushPreferencesOnUnload();
  assert.strictEqual(beacons.length, 0, 'persisted unresolved recovery must not post stale data on pagehide');
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

  api.saveServerPreferences({ signal_history: [{ symbol: 'DOGEUSDT', bias: 'bull' }] });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 8, 'reconciliation failure test must start a preference request');
  const recoveryRequest = JSON.parse(fetchCalls[7].options.body);
  fetchResolvers[7].resolve(responsePayload({ saved: true, applied: false, revision: recoveryRequest.revision + 1 }));
  await settle();
  assert.strictEqual(fetchCalls.length, 9, 'revision conflict must start reconciliation GET');
  fetchResolvers[8].reject(new Error('reconciliation offline'));
  await settle();
  assert(api.preferenceState().conflictRecovery, 'failed reconciliation must retain the rejected patch');
  const newerHistory = [{ symbol: 'DOGEUSDT', bias: 'bear' }];
  api.saveServerPreferences({ signal_history: newerHistory });
  assert.deepStrictEqual(api.preferenceState().conflictRecovery.patch.signal_history, newerHistory, 'newer same-key edit must replace the persisted recovery value');
  const persistedRecovery = Array.from(values.entries()).find(([key]) => key.includes('preference_recovery'));
  assert(persistedRecovery, 'failed reconciliation must persist recovery state outside runtime memory');
  assert.deepStrictEqual(JSON.parse(persistedRecovery[1]).patch.signal_history, newerHistory, 'newer same-key recovery value must survive reload');
  api.clearConflictRecoveryMemory();
  assert.strictEqual(api.preferenceState().conflictRecovery, null, 'test must simulate losing runtime memory');
  api.reloadConflictRecoveryMemory();
  assert(api.preferenceState().conflictRecovery, 'persisted recovery state must load after runtime memory is lost');
  runTimers();
  assert.strictEqual(fetchCalls.length, 10, 'failed reconciliation must retry with another GET');
  assert.strictEqual(fetchCalls[9].options.method, undefined, 'reconciliation retry must not promote the stale patch');
  fetchResolvers[9].resolve(responsePayload({
    preferences: { account_risk: { daily_loss_pct: 1 }, tv_kline_interval: '60' },
    revision: recoveryRequest.revision + 1,
    storage: { mysql: { configured: true, available: true } }
  }));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 11, 'unchanged field must flush after reconciliation recovers');
  const recoveredPatch = JSON.parse(fetchCalls[10].options.body);
  assert.deepStrictEqual(recoveredPatch.preferences.signal_history, newerHistory, 'recovered conflict must use the newest same-key edit');
  fetchResolvers[10].resolve(successfulResponse(fetchCalls[10]));
  await settle();
  assert.strictEqual(
    Array.from(values.keys()).some((key) => key.includes('preference_recovery')),
    false,
    'successful conflict recovery must clear persisted recovery state'
  );

  api.saveServerPreferences({ position_state: { DOGEUSDT: 'long' } });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 12, 'storage failure test must start a preference request');
  fetchResolvers[11].resolve(responsePayload({ saved: false, applied: false, revision: 0 }));
  await settle();
  runTimers();
  assert.strictEqual(fetchCalls.length, 13, 'HTTP 200 with saved=false must retry as a storage outage');
  const storageRetry = JSON.parse(fetchCalls[12].options.body);
  assert.deepStrictEqual(storageRetry.preferences.position_state, { DOGEUSDT: 'long' }, 'storage retry must preserve the patch');
  fetchResolvers[12].resolve(successfulResponse(fetchCalls[12]));
  await settle();

  let preferenceLoadDone = false;
  api.loadServerPreferences(() => { preferenceLoadDone = true; });
  assert.strictEqual(fetchCalls.length, 14, 'storage outage test must start a preference read');
  fetchResolvers[13].resolve(responsePayload({
    preferences: {},
    revision: 0,
    storage: { mysql: { configured: true, available: false } }
  }));
  await settle();
  runTimers();
  assert.strictEqual(preferenceLoadDone, true, 'preference load callback must complete during storage outage');
  assert.strictEqual(fetchCalls.length, 14, 'storage outage must not queue a full local snapshot writeback');

  api.setData([{ symbol: 'DOGEUSDT' }]);
  api.setCurrentSymbol('DOGEUSDT');
  api.setRealtimeState({ transportOpen: true, upstreamConnected: false, lastPayloadAt: nowValue, error: 'TimeoutError' });
  assert.strictEqual(api.realtimeBadgeMode(), 'offline', 'explicit upstream error without a fresh price must be offline');
  api.setRealtimeState({ transportOpen: true, upstreamConnected: false, lastPayloadAt: 0, error: '' });
  assert.strictEqual(api.realtimeBadgeMode(), 'connecting', 'initial SSE handshake without an upstream error may show connecting');

  api.saveServerPreferences({ account_risk: { daily_loss_pct: 3 } });
  api.flushServerPreferences();
  const unloadRequest = JSON.parse(fetchCalls[14].options.body);
  api.saveServerPreferences({ tv_kline_interval: '240' });
  api.flushPreferencesOnUnload();
  assert.strictEqual(beacons.length, 1, 'mixed unload work must use one ordered batch beacon');
  const unloadBatch = JSON.parse(await beacons[0].body.text());
  assert.strictEqual(unloadBatch.patches.length, 2, 'mixed unload batch must preserve both ordered patches');
  const inFlightBeacon = unloadBatch.patches[0];
  const pendingBeacon = unloadBatch.patches[1];
  assert.deepStrictEqual(inFlightBeacon.preferences.account_risk, { daily_loss_pct: 3 }, 'first beacon must carry only the in-flight patch');
  assert.strictEqual(inFlightBeacon.preferences.tv_kline_interval, undefined, 'in-flight beacon must not absorb newer pending fields');
  assert.strictEqual(inFlightBeacon.revision, unloadRequest.revision, 'in-flight unload replay must keep its original revision');
  assert.strictEqual(pendingBeacon.preferences.account_risk, undefined, 'pending beacon must not promote older in-flight fields');
  assert.strictEqual(pendingBeacon.preferences.tv_kline_interval, '240', 'pending beacon must carry the newer field');
  assert(pendingBeacon.revision > inFlightBeacon.revision, 'pending beacon must use a newer revision');
  fetchResolvers[14].resolve(successfulResponse(fetchCalls[14]));
  await settle();

  api.saveServerPreferences({ position_state: { DOGEUSDT: 'short' } });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 16, 'non-retryable failure test must start one preference request');
  fetchResolvers[15].resolve({
    ok: false,
    status: 401,
    json: () => Promise.resolve({ error: 'login required' })
  });
  await settle();
  runTimers();
  runTimers();
  assert.strictEqual(fetchCalls.length, 16, 'HTTP 401 must not create zero-delay preference retries');
  assert.deepStrictEqual(api.preferenceState().pending.position_state, { DOGEUSDT: 'short' }, 'non-retryable failure must retain the local pending patch');

  api.setConflictRecovery({ patch: { tv_kline_interval: '60' }, baseSnapshot: {} });
  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 17, 'recovery version test must start one GET');
  api.saveServerPreferences({ tv_kline_interval: 'D' });
  fetchResolvers[16].resolve(responsePayload({
    preferences: {},
    revision: 1,
    storage: { mysql: { configured: true, available: true } }
  }));
  await settle();
  assert.strictEqual(api.preferenceState().conflictRecovery.patch.tv_kline_interval, 'D', 'older reconciliation completion must not clear a newer recovery version');

  api.flushServerPreferences();
  assert.strictEqual(fetchCalls.length, 18, 'recovery authorization test must start one GET');
  fetchResolvers[17].resolve({
    ok: false,
    status: 401,
    json: () => Promise.resolve({ error: 'login required' })
  });
  await settle();
  runTimers();
  runTimers();
  assert.strictEqual(fetchCalls.length, 18, 'recovery GET must stop automatic retries after HTTP 401');
  assert(api.preferenceState().conflictRecovery, 'non-retryable recovery failure must remain persisted');
  api.setConflictRecovery(null);

  let localFallbackDone = false;
  api.loadServerPreferences(() => { localFallbackDone = true; });
  assert.strictEqual(fetchCalls.length, 19, 'local fallback test must read storage configuration');
  fetchResolvers[18].resolve(responsePayload({
    preferences: {},
    revision: 0,
    storage: { mysql: { configured: false, available: false } }
  }));
  await settle();
  assert.strictEqual(localFallbackDone, true, 'unconfigured storage load must complete');
  assert.strictEqual(api.preferenceState().syncEnabled, false, 'unconfigured MySQL must disable server preference sync');
  api.saveServerPreferences({ position_state: { DOGEUSDT: 'short' } });
  api.flushServerPreferences();
  runTimers();
  assert.strictEqual(fetchCalls.length, 19, 'localStorage fallback must not retry preference POSTs');

  api.setData(Array.from({ length: 7 }, (_, index) => ({ symbol: `S${index}USDT` })));
  api.addSymbol('NEW1', 'history');
  api.addSymbol('NEW2', 'history');
  assert.strictEqual(fetchCalls.length, 20, 'pending symbol reservation must prevent concurrent requests from exceeding capacity');
  assert.strictEqual(api.pendingSymbolAddCount(), 1, 'one in-flight symbol must reserve the final slot');
  api.setData(Array.from({ length: 8 }, (_, index) => ({ symbol: `R${index}USDT` })));
  fetchResolvers[19].resolve(responsePayload({ data: [{ symbol: 'NEW1USDT', last: 1 }], generated_at: '2026-07-17 00:00:00' }));
  await settle();
  assert.strictEqual(api.getData().length, 8, 'late add response must recheck capacity before mutating DATA');
  assert.strictEqual(api.pendingSymbolAddCount(), 0, 'completed add request must release its reservation');

  let failedAuthReady = null;
  api.loadCurrentUser((ready) => { failedAuthReady = ready; });
  assert.strictEqual(fetchCalls.length, 21, 'auth state check must issue one request');
  fetchResolvers[20].resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: 'auth unavailable' }) });
  await settle();
  assert.strictEqual(failedAuthReady, false, 'auth service failure must stop protected dashboard startup');
  assert.strictEqual(api.preferenceState().currentUser, null, 'auth failure must not select the shared local user scope');
  assert.strictEqual(api.preferenceState().syncEnabled, false, 'auth failure must disable preference synchronization');

  let localAuthReady = null;
  api.loadCurrentUser((ready) => { localAuthReady = ready; });
  fetchResolvers[21].resolve(responsePayload({ auth_enabled: false, authenticated: true, user: { id: 0, username: 'local', role: 'admin' } }));
  await settle();
  assert.strictEqual(localAuthReady, true, 'explicit auth-disabled response may select local mode');
  assert.strictEqual(api.preferenceState().currentUser.id, 0, 'explicit local mode must retain its isolated user id');

  console.log('frontend smoke ok');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
