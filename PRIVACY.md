# Privacy Policy — AgentMesh

**Last updated: June 2026**

## Summary

AgentMesh does not collect, transmit, or store any personal data on external servers. All data stays on your local machine.

## What the Chrome extension does

The AgentMesh Chrome extension:

- Intercepts prompts you type into ChatGPT, Claude.ai, and Gemini **before** they are sent
- Forwards those prompts to a **locally running proxy** on your own machine (localhost:8080 by default)
- Displays governance metadata (cache hit/miss, quota usage, tokens saved) in a popup

## Data collected

| Data | Where it goes | Stored externally? |
|---|---|---|
| Prompt text | Local proxy only (localhost) | No |
| Cache hit/miss stats | chrome.storage.local (your browser) | No |
| Tokens saved / cost saved | chrome.storage.local (your browser) | No |
| Proxy port setting | chrome.storage.sync (your Google account) | No |

## What we do NOT do

- We do not send any data to AgentMesh servers (there are none)
- We do not collect analytics or telemetry
- We do not sell, share, or transmit any user data to third parties
- We do not store prompt content beyond the local proxy cache on your machine

## Local proxy

The AgentMesh proxy runs entirely on your own machine. Prompt data sent to `localhost` never leaves your device unless you explicitly forward it to an LLM API (Anthropic, OpenAI, Google) — the same call your browser would have made anyway.

## Contact

Questions? Open an issue at [github.com/anilatambharii/agentmesh/issues](https://github.com/anilatambharii/agentmesh/issues) or email anil@ambharii.com.
