"""
Slack and PagerDuty Webhook Alerts

Sends real-time governance alerts when anomalies, quota blocks,
or PII detections occur.

Supported channels:
  - Slack (incoming webhook)
  - PagerDuty Events API v2
  - Generic HTTP webhook (JSON POST)

Usage:
  from agentmesh.integrations.webhooks import AlertRouter, SlackConfig, PagerDutyConfig

  router = AlertRouter(
      slack=SlackConfig(webhook_url="https://hooks.slack.com/services/..."),
      pagerduty=PagerDutyConfig(routing_key="abc123"),
  )
  router.alert(
      title="Runaway loop detected",
      message="Team 'engineering' made 47 calls in 10s",
      severity="critical",
      team="engineering",
  )
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


@dataclass
class SlackConfig:
    webhook_url: str
    channel:     Optional[str] = None    # override default channel
    username:    str           = "AgentMesh"
    icon_emoji:  str           = ":shield:"


@dataclass
class PagerDutyConfig:
    routing_key:  str
    source:       str = "agentmesh-proxy"
    component:    str = "ai-governance"


@dataclass
class WebhookConfig:
    url:     str
    headers: Dict[str, str] = field(default_factory=dict)
    method:  str            = "POST"


@dataclass
class Alert:
    title:    str
    message:  str
    severity: AlertSeverity
    team:     str       = ""
    metadata: dict      = field(default_factory=dict)
    ts:       float     = field(default_factory=time.time)


# ── Severity → Slack colour ───────────────────────────────────────────────────
_SLACK_COLOUR = {
    AlertSeverity.INFO:     "#36a64f",   # green
    AlertSeverity.WARNING:  "#ff9f00",   # amber
    AlertSeverity.CRITICAL: "#e01e5a",   # red
}

_SLACK_EMOJI = {
    AlertSeverity.INFO:     ":white_check_mark:",
    AlertSeverity.WARNING:  ":warning:",
    AlertSeverity.CRITICAL: ":rotating_light:",
}

# ── PagerDuty severity mapping ────────────────────────────────────────────────
_PD_SEVERITY = {
    AlertSeverity.INFO:     "info",
    AlertSeverity.WARNING:  "warning",
    AlertSeverity.CRITICAL: "critical",
}


def _post(url: str, payload: dict, headers: Optional[Dict[str, str]] = None) -> bool:
    """HTTP POST JSON payload. Returns True on success."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status < 300
    except urllib.error.HTTPError as e:
        logger.warning("Webhook POST failed: %s %s", e.code, e.reason)
        return False
    except Exception as e:
        logger.warning("Webhook POST error: %s", e)
        return False


class _SlackSender:
    def __init__(self, cfg: SlackConfig):
        self.cfg = cfg

    def send(self, alert: Alert) -> bool:
        colour = _SLACK_COLOUR.get(alert.severity, "#aaaaaa")
        emoji  = _SLACK_EMOJI.get(alert.severity, ":bell:")
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(alert.ts))

        payload: dict = {
            "username": self.cfg.username,
            "icon_emoji": self.cfg.icon_emoji,
            "attachments": [{
                "color": colour,
                "title": f"{emoji} {alert.title}",
                "text":  alert.message,
                "fields": [
                    {"title": "Team",     "value": alert.team or "—",          "short": True},
                    {"title": "Severity", "value": alert.severity.value.upper(), "short": True},
                    {"title": "Time",     "value": ts_str,                      "short": False},
                ] + [
                    {"title": k, "value": str(v), "short": True}
                    for k, v in alert.metadata.items()
                ],
                "footer": "AgentMesh Governance",
            }],
        }
        if self.cfg.channel:
            payload["channel"] = self.cfg.channel

        return _post(self.cfg.webhook_url, payload)


class _PagerDutySender:
    _API = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, cfg: PagerDutyConfig):
        self.cfg = cfg

    def send(self, alert: Alert) -> bool:
        payload = {
            "routing_key":  self.cfg.routing_key,
            "event_action": "trigger",
            "payload": {
                "summary":   alert.title,
                "severity":  _PD_SEVERITY.get(alert.severity, "warning"),
                "source":    self.cfg.source,
                "component": self.cfg.component,
                "group":     alert.team or "unknown",
                "custom_details": {
                    "message":  alert.message,
                    **alert.metadata,
                },
            },
        }
        return _post(self._API, payload)


class _WebhookSender:
    def __init__(self, cfg: WebhookConfig):
        self.cfg = cfg

    def send(self, alert: Alert) -> bool:
        payload = {
            "title":    alert.title,
            "message":  alert.message,
            "severity": alert.severity.value,
            "team":     alert.team,
            "ts":       alert.ts,
            **alert.metadata,
        }
        return _post(self.cfg.url, payload, self.cfg.headers)


class AlertRouter:
    """
    Route governance alerts to Slack, PagerDuty, and/or generic webhooks.

    Sends asynchronously in a daemon thread so alerts never block the
    hot request path.

    Args:
        slack:       SlackConfig (optional)
        pagerduty:   PagerDutyConfig (optional)
        webhooks:    List of WebhookConfig (optional)
        min_severity: Only send alerts at or above this level
                      Default: WARNING
    """

    def __init__(
        self,
        slack:        Optional[SlackConfig]      = None,
        pagerduty:    Optional[PagerDutyConfig]  = None,
        webhooks:     Optional[List[WebhookConfig]] = None,
        min_severity: AlertSeverity              = AlertSeverity.WARNING,
    ):
        self._senders = []
        if slack:
            self._senders.append(_SlackSender(slack))
        if pagerduty:
            self._senders.append(_PagerDutySender(pagerduty))
        for wh in (webhooks or []):
            self._senders.append(_WebhookSender(wh))

        order = [AlertSeverity.INFO, AlertSeverity.WARNING, AlertSeverity.CRITICAL]
        self._min_idx = order.index(min_severity)
        self._order   = order

    def alert(
        self,
        title:    str,
        message:  str,
        severity: str  = "warning",
        team:     str  = "",
        **metadata,
    ) -> None:
        """Fire-and-forget alert. Never raises; errors are logged."""
        sev = AlertSeverity(severity) if isinstance(severity, str) else severity
        if self._order.index(sev) < self._min_idx:
            return
        a = Alert(title=title, message=message, severity=sev,
                  team=team, metadata=metadata)
        threading.Thread(target=self._send_all, args=(a,), daemon=True).start()

    def _send_all(self, alert: Alert) -> None:
        for sender in self._senders:
            try:
                sender.send(alert)
            except Exception as e:
                logger.warning("Alert sender %s failed: %s", type(sender).__name__, e)
