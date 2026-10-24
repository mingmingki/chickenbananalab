"""Execute the shipped browser functions against delayed responses and lifecycle events."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js browser-function runtime required")


def function(name):
    source = (ROOT / "templates/dashboard.html").read_text()
    match = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\)\s*\{", source)
    assert match, f"missing browser function {name}"
    end = source.index("\n}", match.end()) + 2
    return source[match.start():end]


def run_js(source, scenario):
    script = "const assert = require('node:assert/strict');\n" + source
    script += "\n(async () => {\n" + scenario + "\n})().catch(e => { console.error(e); process.exitCode = 1; });"
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=8)
    assert result.returncode == 0, result.stdout + result.stderr


def test_slow_optional_statistics_do_not_delay_core_rendering():
    run_js(function("refreshState"), """
let reached = false;
const state = {symbols: {}, get adaptive_exit() {
  reached = true;
  throw new Error('core-render-reached');
}};
global.renderMarketContext = () => {};
global.renderStrategyGuide = () => {};
global.fetchWithTimeout = url => url === '/api/state'
  ? Promise.resolve({ok: true, json: async () => state})
  : new Promise(() => {}); // Optional statistics never respond.
const refresh = refreshState().catch(e => {
  assert.equal(e.message, 'core-render-reached');
});
await Promise.race([refresh, new Promise(resolve => setTimeout(resolve, 150))]);
assert.equal(reached, true, 'core rendering waited for optional statistics');
""")


def test_manual_refresh_updates_balances_and_positions_before_optional_panels():
    run_js(function("refreshAllNow"), """
const calls = [];
for (const name of ['refreshState', 'refreshCandidateC', 'refreshPnlSummary',
                   'refreshOperatingCosts', 'refreshTradesFiltered', 'refreshShadow',
                   'refreshMarketStructure', 'refreshLogs', 'refreshOpsObservability']) {
  global[name] = async () => { calls.push(name); };
}
global.refreshOperatingCosts = () => {
  calls.push('refreshOperatingCosts');
  return new Promise(() => {});
};
refreshAllNow();
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(calls.slice(0, 3), ['refreshState', 'refreshCandidateC', 'refreshPnlSummary']);
""")


def test_concurrent_refreshes_share_one_request_and_recover_after_failure():
    run_js(function("coalesceRefresh"), """
let count = 0, resolve, reject;
const refresh = coalesceRefresh(() => {
  count += 1;
  return new Promise((yes, no) => { resolve = yes; reject = no; });
});
const first = refresh(), second = refresh();
assert.strictEqual(first, second, 'same panel sent duplicate simultaneous requests');
await Promise.resolve();
assert.equal(count, 1);
resolve('fresh');
assert.equal(await first, 'fresh');
const failed = refresh();
await Promise.resolve();
assert.equal(count, 2);
reject(new Error('temporary'));
await assert.rejects(failed, /temporary/);
const recovered = refresh();
await Promise.resolve();
assert.equal(count, 3);
resolve('recovered');
assert.equal(await recovered, 'recovered');
""")


def test_returning_to_visible_app_refreshes_critical_panel_without_waiting_for_timer():
    run_js(function("guardedPoll"), """
const listeners = {}, intervals = [];
global.document = {hidden: true, addEventListener: (name, fn) => { listeners[name] = fn; }};
global.setInterval = fn => { intervals.push(fn); };
let calls = 0, release;
const refresh = () => { calls += 1; return new Promise(resolve => { release = resolve; }); };
guardedPoll(refresh, 2000, true);
assert.equal(calls, 0);
document.hidden = false;
if (listeners.visibilitychange) listeners.visibilitychange();
await Promise.resolve();
assert.equal(calls, 1, 'visible app did not refresh immediately');
intervals[0]();
assert.equal(calls, 1, 'in-flight refresh overlapped');
release();
await new Promise(resolve => setImmediate(resolve));
document.hidden = true;
intervals[0]();
assert.equal(calls, 1, 'hidden app refreshed');
""")


def test_observability_fetch_does_not_send_orders_or_paid_provider_requests():
    run_js(function("refreshOpsObservability"), """
const calls = [];
global.fetchWithTimeout = async url => {
  calls.push(url);
  return {ok: true, json: async () => ({snapshot: {generated_at: 'now'}})};
};
let rendered;
global.renderOpsObservability = value => { rendered = value; };
await refreshOpsObservability();
assert.deepEqual(calls, ['/api/ops-observability']);
assert.deepEqual(rendered, {generated_at: 'now'});
""")
