# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.2.x | ✅ |
| 0.1.x | Security fixes only |
| < 0.1 | ❌ |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report security issues by emailing: **meetanilp@gmail.com**  
Subject line: `[SECURITY] AgentMesh — <brief description>`

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to release a fix within 14 days for critical issues.

## Security Design

AgentMesh is designed with security in mind:

- **Tamper-evident audit trail**: Ed25519 signatures + SHA-256 hash chains prevent log tampering
- **PII detection**: Optional PII scanning before LLM calls (prevents accidental PHI leakage)
- **Hard stops**: Budget enforcement can kill agent runs before they leak data via prompt injection loops
- **No network calls in core**: The core library makes no network calls itself; all LLM calls are made by the wrapped framework
- **Zero external state**: Default deployment is fully in-process with no external database
- **Data residency**: Policy enforcement can restrict which regions data flows through

## Known Limitations

- The heuristic PII detector is regex-based; it does not replace a dedicated DLP solution
- The HTTP proxy mode does not currently support mutual TLS (v0.3 roadmap)
- Audit trail is in-memory by default; for persistent audit trails, export to SIEM via OpenTelemetry
