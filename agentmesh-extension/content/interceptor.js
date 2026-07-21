/**
 * AgentMesh Content Script — Prompt Governance Observer
 *
 * Injected into: claude.ai, chat.openai.com, chatgpt.com, gemini.google.com
 *
 * Flow:
 *  1. Detect the AI chat input + send button via site-specific selectors
 *  2. Read the prompt at the moment of submit (capture phase, read-only —
 *     never blocks or delays the real send)
 *  3. Report it to the background worker -> AgentMesh proxy dry-run check,
 *     asynchronously, after the message is already on its way
 *  4. If the check surfaces something worth knowing (cache hit, compression
 *     available, quota warning), flash a brief auto-dismissing toast
 *  5. If there's nothing notable, or AgentMesh is offline/slow, say nothing
 *     — zero friction, every time
 *
 * This used to preventDefault() the send and gate it behind a proxy round
 * trip, requiring a second click on every message. It never actually
 * gated anything real: none of these sites' web apps call
 * api.anthropic.com/api.openai.com directly from the browser, so the
 * governance check was informational-only to begin with. Blocking the UI
 * for it was pure friction — this makes it fire-and-forget instead.
 */

(function () {
  'use strict';

  // ── Site detection ──────────────────────────────────────────────────────────

  const HOST = location.hostname;
  const SITE =
    HOST.includes('claude.ai')     ? 'claude'  :
    HOST.includes('chatgpt.com') || HOST.includes('chat.openai.com') ? 'chatgpt' :
    HOST.includes('gemini.google') ? 'gemini'  : null;

  if (!SITE) return;

  // ── Per-site selectors ──────────────────────────────────────────────────────

  const SEL = {
    claude: {
      input:   'div[contenteditable="true"][data-placeholder], div[contenteditable="true"].ProseMirror',
      sendBtn: 'button[aria-label*="Send"], button[data-testid*="send"]',
    },
    chatgpt: {
      input:   '#prompt-textarea, div[contenteditable="true"][data-id="root"], div[contenteditable="true"].ProseMirror',
      sendBtn: 'button[data-testid="send-button"], button[aria-label*="Send message"]',
    },
    gemini: {
      input:   '.ql-editor[contenteditable="true"], rich-textarea [contenteditable="true"]',
      sendBtn: 'button[aria-label*="Send"], button.send-button, mat-icon[data-mat-icon-name="send"]',
    },
  }[SITE];

  // ── Toast overlay (informational only — never requires a decision) ─────────

  let overlayEl  = null;
  let hideTimer  = null;

  function ensureOverlay() {
    if (overlayEl) return overlayEl;
    overlayEl = document.createElement('div');
    overlayEl.id = 'agentmesh-overlay';
    overlayEl.innerHTML = `
      <div class="am-header">
        <span class="am-logo">&#x2B21; AgentMesh</span>
        <button class="am-x" id="am-x" title="Dismiss">&#x2715;</button>
      </div>
      <div class="am-body" id="am-body"></div>
    `;
    document.body.appendChild(overlayEl);
    overlayEl.querySelector('#am-x').addEventListener('click', hideOverlay);
    return overlayEl;
  }

  function showToast(html, autoHideMs = 2500) {
    const el = ensureOverlay();
    el.querySelector('#am-body').innerHTML = html;
    el.style.display = 'block';
    clearTimeout(hideTimer);
    if (autoHideMs) hideTimer = setTimeout(hideOverlay, autoHideMs);
  }

  function hideOverlay() {
    clearTimeout(hideTimer);
    if (overlayEl) overlayEl.style.display = 'none';
  }

  function renderResult(result, originalPrompt) {
    if (!result || !result.ok) return; // proxy offline/slow — stay silent, zero friction

    const { cacheStatus, quotaPct, isCompressed } = result;
    const quotaNum = parseFloat(quotaPct) || 0;

    const chips = [];
    if (cacheStatus === 'hit') {
      const saved = Math.ceil(originalPrompt.length / 4);
      chips.push(`<div class="am-chip am-green">Cache HIT &mdash; ~${saved.toLocaleString()} tokens saved on your next identical prompt</div>`);
    }
    if (quotaNum >= 80) {
      chips.push(`<div class="am-chip ${quotaNum >= 95 ? 'am-red' : 'am-yellow'}">Team quota: ${quotaPct}</div>`);
    }
    if (isCompressed) {
      chips.push(`<div class="am-chip am-purple">This prompt qualifies for AgentMesh compression</div>`);
    }

    if (!chips.length) return; // nothing worth surfacing — say nothing

    showToast(chips.join(''));
  }

  // ── Prompt text helper ──────────────────────────────────────────────────────

  function readPrompt(el) {
    if (!el) return '';
    return (el.innerText || el.value || el.textContent || '').trim();
  }

  // ── Fire-and-forget governance check — reports AFTER the real send ─────────

  function reportPrompt(prompt) {
    if (!prompt) return;
    chrome.storage.sync.get(['agentmeshTeam', 'agentmeshUser'], prefs => {
      chrome.runtime.sendMessage(
        {
          type:   'OPTIMIZE_PROMPT',
          prompt,
          tool:   SITE,
          team:   prefs.agentmeshTeam || '',
          user:   prefs.agentmeshUser || '',
        },
        response => {
          if (chrome.runtime.lastError) return; // extension context gone — ignore
          renderResult(response && response.result, prompt);
        },
      );
    });
  }

  // ── Hooks: read the prompt at submit time, never block it ──────────────────
  //
  // Listeners are registered on the CAPTURE phase purely so we read the text
  // before the site's own handler clears the input — not to intercept or
  // cancel anything. No preventDefault, no stopPropagation: the native send
  // always fires immediately and normally.

  function hookButton(btn) {
    if (btn._amHooked) return;
    btn._amHooked = true;
    btn.addEventListener('click', () => {
      reportPrompt(readPrompt(document.querySelector(SEL.input)));
    }, true /* capture, read-only */);
  }

  function hookInput(input) {
    if (input._amKbHooked) return;
    input._amKbHooked = true;
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      reportPrompt(readPrompt(input));
    }, true /* capture, read-only */);
  }

  // ── MutationObserver: attach hooks as DOM renders ───────────────────────────

  function attachHooks() {
    const btn   = document.querySelector(SEL.sendBtn);
    const input = document.querySelector(SEL.input);
    if (btn)   hookButton(btn);
    if (input) hookInput(input);
  }

  const observer = new MutationObserver(attachHooks);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  attachHooks(); // immediate attempt

})();
