/**
 * AgentMesh Extension — Background Service Worker
 *
 * Responsibilities:
 *  1. Health-check the local AgentMesh proxy and update the toolbar badge
 *  2. Receive OPTIMIZE_PROMPT messages from content scripts, forward to proxy
 *     with X-AgentMesh-Dry-Run: true, return governance metadata
 *  3. Track per-session stats (intercepted, cache hits, tokens/cost saved)
 *  4. Dynamically update declarativeNetRequest rules when user changes port
 */

'use strict';

// ── Config ────────────────────────────────────────────────────────────────────

const DEFAULT_PORT = 8080;

function proxyUrl(port) {
  return `http://localhost:${port}`;
}

// ── Session stats (persisted to chrome.storage.local — survives SW restarts) ──

const stats = {
  intercepted:  0,
  cacheHits:    0,
  tokensSaved:  0,
  costSavedUsd: 0,
  optimized:    0,
};

// Restore from local storage on startup
chrome.storage.local.get(['agentmeshStats'], d => {
  if (d.agentmeshStats) Object.assign(stats, d.agentmeshStats);
});

function _persistStats() {
  chrome.storage.local.set({ agentmeshStats: { ...stats } });
}

// ── Health check ──────────────────────────────────────────────────────────────

async function getPort() {
  return new Promise(resolve => {
    chrome.storage.sync.get(['proxyPort'], d => resolve(d.proxyPort || DEFAULT_PORT));
  });
}

async function checkHealth() {
  try {
    const port = await getPort();
    const r = await fetch(`${proxyUrl(port)}/health`, {
      signal: AbortSignal.timeout(2500),
    });
    return r.ok;
  } catch {
    return false;
  }
}

async function refreshBadge() {
  const ok = await checkHealth();
  chrome.action.setBadgeText({ text: ok ? 'ON' : 'OFF' });
  chrome.action.setBadgeBackgroundColor({ color: ok ? '#22c55e' : '#ef4444' });
  // Persist so popup can read it immediately
  chrome.storage.local.set({ proxyOnline: ok });
}

// ── Optimize prompt via proxy dry-run ─────────────────────────────────────────

async function optimizePrompt({ prompt, tool, team, user }) {
  const port = await getPort();
  const base = proxyUrl(port);

  const headers = {
    'Content-Type':     'application/json',
    'x-api-key':        'agentmesh',
    'X-AgentMesh-Tool': tool || 'browser-extension',
  };
  if (team) headers['X-AgentMesh-Team'] = team;
  if (user) headers['X-AgentMesh-User'] = user;

  try {
    const r = await fetch(`${base}/v1/messages`, {
      method:  'POST',
      headers,
      body: JSON.stringify({
        model:      'claude-haiku-4-5',
        max_tokens: 256,
        messages:   [{ role: 'user', content: prompt }],
      }),
      signal: AbortSignal.timeout(8000),
    });

    const cacheStatus  = r.headers.get('x-agentmesh-cache')     || 'miss';
    const tokens       = parseInt(r.headers.get('x-agentmesh-tokens') || '0', 10);
    const quotaPct     = r.headers.get('x-agentmesh-quota-pct') || '0%';
    const isCompressed = r.headers.get('x-agentmesh-compressed') === 'true';
    const vendor       = r.headers.get('x-agentmesh-vendor')    || '';
    const costUsd      = parseFloat(r.headers.get('x-agentmesh-cost-usd') || '0');

    // Pull optimized content if the proxy returned one
    let optimizedPrompt = null;
    try {
      const body = await r.json();
      const text = body?.content?.[0]?.text;
      if (text && text !== prompt) optimizedPrompt = text;
    } catch { /* ignore */ }

    // Update stats
    stats.intercepted++;
    if (cacheStatus === 'hit') {
      stats.cacheHits++;
      // Rough saving: tokens that would have been sent
      const saved = Math.ceil(prompt.length / 4);
      stats.tokensSaved  += saved;
      stats.costSavedUsd += saved * 0.00000025; // Haiku input price baseline
    }
    if (isCompressed) stats.optimized++;
    _persistStats();

    return { ok: true, cacheStatus, tokens, quotaPct, isCompressed, vendor, costUsd, optimizedPrompt };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

// ── Dynamic rule update (port change) ─────────────────────────────────────────

async function updateRedirectRules(port) {
  const transform = { scheme: 'http', host: 'localhost', port: String(port) };
  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: [101, 102],
    addRules: [
      {
        id: 101, priority: 2,
        action: { type: 'redirect', redirect: { transform } },
        condition: { urlFilter: '||api.anthropic.com/', resourceTypes: ['xmlhttprequest'] },
      },
      {
        id: 102, priority: 2,
        action: { type: 'redirect', redirect: { transform } },
        condition: { urlFilter: '||api.openai.com/', resourceTypes: ['xmlhttprequest'] },
      },
    ],
  });
}

// ── Message handler ───────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  switch (msg.type) {

    case 'CHECK_HEALTH':
      checkHealth().then(ok => sendResponse({ ok }));
      return true;

    case 'OPTIMIZE_PROMPT':
      optimizePrompt(msg).then(result => sendResponse({ result }));
      return true;

    case 'GET_STATS':
      chrome.storage.local.get(['agentmeshStats', 'proxyOnline'], d => {
        sendResponse({ stats: d.agentmeshStats || stats, online: d.proxyOnline || false });
      });
      return true;

    case 'SET_PORT':
      updateRedirectRules(msg.port)
        .then(() => sendResponse({ ok: true }))
        .catch(e => sendResponse({ ok: false, error: e.message }));
      return true;
  }
});

// ── Storage change listener (port saved from popup) ───────────────────────────

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.proxyPort) {
    updateRedirectRules(changes.proxyPort.newValue || DEFAULT_PORT);
    refreshBadge();
  }
});

// ── Startup ───────────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(async () => {
  const port = await getPort();
  await updateRedirectRules(port);
  refreshBadge();
});

chrome.runtime.onStartup.addListener(refreshBadge);

// Refresh badge every 30 seconds
setInterval(refreshBadge, 30_000);
refreshBadge();
