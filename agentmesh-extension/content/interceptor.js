/**
 * AgentMesh Content Script — Prompt Interceptor
 *
 * Injected into: claude.ai, chat.openai.com, chatgpt.com, gemini.google.com
 *
 * Flow:
 *  1. Detect the AI chat input + send button via site-specific selectors
 *  2. Capture submit (click or Enter) before it reaches the page
 *  3. Send prompt to background worker → AgentMesh proxy (dry-run)
 *  4. Show a small overlay with: cache status, quota %, compression offer
 *  5. Auto-proceed after 3 s timeout OR when user chooses "Send" / "Send Optimized"
 *  6. If AgentMesh is offline, pass through immediately — zero friction
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

  // ── State ───────────────────────────────────────────────────────────────────

  let overlayEl      = null;
  let pendingSubmit  = null;   // fn(optimizedPrompt | null)
  let isIntercepting = false;
  let skipNext       = false;  // bypass flag after we re-fire

  // ── Overlay ─────────────────────────────────────────────────────────────────

  function ensureOverlay() {
    if (overlayEl) return overlayEl;

    overlayEl = document.createElement('div');
    overlayEl.id = 'agentmesh-overlay';
    overlayEl.innerHTML = `
      <div class="am-header">
        <span class="am-logo">&#x2B21; AgentMesh</span>
        <button class="am-x" id="am-x" title="Dismiss and send original">&#x2715;</button>
      </div>
      <div class="am-body" id="am-body">
        <span class="am-spin"></span> Checking governance&hellip;
      </div>
      <div class="am-footer" id="am-footer">
        <button class="am-btn am-primary" id="am-send-opt" style="display:none">Send Optimized</button>
        <button class="am-btn am-secondary" id="am-send-orig">Send as-is</button>
      </div>
    `;
    document.body.appendChild(overlayEl);

    overlayEl.querySelector('#am-x').addEventListener('click', () => {
      dismiss(null);
    });
    overlayEl.querySelector('#am-send-orig').addEventListener('click', () => {
      dismiss(null);
    });
    overlayEl.querySelector('#am-send-opt').addEventListener('click', () => {
      dismiss(overlayEl.dataset.optimizedPrompt || null);
    });

    return overlayEl;
  }

  function showOverlay() {
    const el = ensureOverlay();
    el.dataset.optimizedPrompt = '';
    el.querySelector('#am-body').innerHTML = '<span class="am-spin"></span> Checking governance&hellip;';
    el.querySelector('#am-send-opt').style.display = 'none';
    el.style.display = 'block';
  }

  function hideOverlay() {
    if (overlayEl) overlayEl.style.display = 'none';
  }

  function renderResult(result, originalPrompt) {
    const el = ensureOverlay();
    const body   = el.querySelector('#am-body');
    const optBtn = el.querySelector('#am-send-opt');

    if (!result || !result.ok) {
      // Proxy offline or timed out — auto-proceed silently
      hideOverlay();
      if (pendingSubmit) pendingSubmit(null);
      pendingSubmit  = null;
      isIntercepting = false;
      return;
    }

    const { cacheStatus, quotaPct, isCompressed, optimizedPrompt } = result;
    const quotaNum = parseFloat(quotaPct) || 0;

    let html = '';

    if (cacheStatus === 'hit') {
      const saved = Math.ceil(originalPrompt.length / 4);
      html += `<div class="am-chip am-green">Cache HIT &mdash; ~${saved.toLocaleString()} tokens saved</div>`;
    } else {
      html += `<div class="am-chip am-blue">Cache MISS &mdash; new request</div>`;
    }

    if (quotaNum >= 80) {
      const color = quotaNum >= 95 ? 'am-red' : 'am-yellow';
      html += `<div class="am-chip ${color}">Team quota: ${quotaPct}</div>`;
    }

    if (isCompressed && optimizedPrompt && optimizedPrompt !== originalPrompt) {
      const pctSaved = Math.round((1 - optimizedPrompt.length / originalPrompt.length) * 100);
      html += `<div class="am-chip am-purple">Compressed &mdash; ~${pctSaved}% shorter</div>`;
      el.dataset.optimizedPrompt = optimizedPrompt;
      optBtn.style.display = 'block';
    }

    body.innerHTML = html || '<div class="am-chip am-blue">Governed &mdash; proceeding</div>';

    // Auto-proceed for cache hits (no user choice needed)
    if (cacheStatus === 'hit' && !isCompressed) {
      setTimeout(() => dismiss(null), 1800);
    }
  }

  function dismiss(optimizedPrompt) {
    hideOverlay();
    isIntercepting = false;
    if (pendingSubmit) {
      const fn = pendingSubmit;
      pendingSubmit = null;
      fn(optimizedPrompt);
    }
  }

  // ── Prompt text helpers ─────────────────────────────────────────────────────

  function readPrompt() {
    const el = document.querySelector(SEL.input);
    if (!el) return '';
    return (el.innerText || el.value || el.textContent || '').trim();
  }

  function writePrompt(text) {
    const el = document.querySelector(SEL.input);
    if (!el || !text) return;

    if (el.isContentEditable) {
      el.focus();
      // Use execCommand so React/Vue state picks up the change
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, text);
    } else {
      // Plain textarea
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      if (setter) {
        setter.call(el, text);
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
  }

  // ── Core intercept logic ────────────────────────────────────────────────────

  function intercept(originalPrompt, resendFn) {
    if (isIntercepting || skipNext) return;
    isIntercepting = true;

    showOverlay();

    // Safety: auto-proceed after 4 s regardless
    const timer = setTimeout(() => {
      if (isIntercepting) dismiss(null);
    }, 4000);

    pendingSubmit = (optimizedPrompt) => {
      clearTimeout(timer);
      if (optimizedPrompt && optimizedPrompt !== originalPrompt) {
        writePrompt(optimizedPrompt);
        setTimeout(() => {
          skipNext = true;
          resendFn();
        }, 120);
      } else {
        skipNext = true;
        resendFn();
      }
    };

    // Load team/user prefs then call proxy
    chrome.storage.sync.get(['agentmeshTeam', 'agentmeshUser'], prefs => {
      chrome.runtime.sendMessage(
        {
          type:   'OPTIMIZE_PROMPT',
          prompt: originalPrompt,
          tool:   SITE,
          team:   prefs.agentmeshTeam || '',
          user:   prefs.agentmeshUser || '',
        },
        response => {
          if (chrome.runtime.lastError || !response) {
            dismiss(null); // extension context gone or proxy unreachable
          } else {
            renderResult(response.result, originalPrompt);
          }
        },
      );
    });
  }

  // ── Hook: send button ───────────────────────────────────────────────────────

  function hookButton(btn) {
    if (btn._amHooked) return;
    btn._amHooked = true;

    btn.addEventListener('click', e => {
      if (skipNext) { skipNext = false; return; }

      const prompt = readPrompt();
      if (!prompt || isIntercepting) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      intercept(prompt, () => {
        btn._amHooked = false;
        btn.click();
        btn._amHooked = true;
      });
    }, true /* capture */);
  }

  // ── Hook: Enter key ─────────────────────────────────────────────────────────

  function hookInput(input) {
    if (input._amKbHooked) return;
    input._amKbHooked = true;

    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      if (skipNext) { skipNext = false; return; }

      const prompt = (input.innerText || input.value || '').trim();
      if (!prompt || isIntercepting) return;

      e.preventDefault();
      e.stopImmediatePropagation();

      intercept(prompt, () => {
        input.dispatchEvent(
          new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
        );
      });
    }, true /* capture */);
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
