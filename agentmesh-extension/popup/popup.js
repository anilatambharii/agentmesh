'use strict';

// ── Helpers ───────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function fmtNum(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

// ── Status ────────────────────────────────────────────────────────────────────

function setStatus(online) {
  const dot   = $('status-dot');
  const label = $('status-label');
  if (online) {
    dot.className   = 'status-dot online';
    label.textContent = 'Connected';
  } else {
    dot.className   = 'status-dot offline';
    label.textContent = 'Offline';
  }
}

// ── Stats ─────────────────────────────────────────────────────────────────────

function renderStats(s) {
  $('s-intercepted').textContent = fmtNum(s.intercepted  || 0);
  $('s-hits').textContent        = fmtNum(s.cacheHits    || 0);
  $('s-tokens').textContent      = fmtNum(s.tokensSaved  || 0);
  $('s-cost').textContent        = '$' + (s.costSavedUsd || 0).toFixed(3);
}

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  // 1. Health + stats from service worker
  chrome.runtime.sendMessage({ type: 'GET_STATS' }, res => {
    if (chrome.runtime.lastError) { setStatus(false); return; }
    setStatus(res?.online || false);
    renderStats(res?.stats || {});
  });

  // 2. Load saved settings
  chrome.storage.sync.get(['agentmeshTeam', 'agentmeshUser', 'proxyPort'], prefs => {
    if (prefs.agentmeshTeam) $('f-team').value = prefs.agentmeshTeam;
    if (prefs.agentmeshUser) $('f-user').value = prefs.agentmeshUser;
    if (prefs.proxyPort)     $('f-port').value = prefs.proxyPort;

    // Update hint links to reflect port
    const port = prefs.proxyPort || 8080;
    updateLinks(port);
  });
}

function updateLinks(port) {
  // Proxy health link reflects current port
  const healthLink = document.querySelector('a[href*="localhost:8080/health"]');
  if (healthLink) healthLink.href = `http://localhost:${port}/health`;
}

// ── Save ──────────────────────────────────────────────────────────────────────

$('btn-save').addEventListener('click', () => {
  const team = $('f-team').value.trim();
  const user = $('f-user').value.trim();
  const port = parseInt($('f-port').value, 10) || 8080;

  chrome.storage.sync.set({ agentmeshTeam: team, agentmeshUser: user, proxyPort: port }, () => {
    // Tell service worker to update dynamic redirect rules for the new port
    chrome.runtime.sendMessage({ type: 'SET_PORT', port }, () => {
      const btn = $('btn-save');
      btn.textContent = 'Saved!';
      btn.classList.add('success');
      setTimeout(() => {
        btn.textContent = 'Save settings';
        btn.classList.remove('success');
      }, 1800);
      updateLinks(port);
    });
  });
});

init();
