"""
MCP Tool-Call Governance Wrapper

Wraps any MCP (Model Context Protocol) stdio server with the same
governance AgentMesh applies to LLM calls: PII/PHI/PCI scanning, prompt
injection detection, scope enforcement, human-in-the-loop approval, and
tamper-evident audit logging — all before a tool call's arguments ever
reach the wrapped server.

This closes a gap LLM-call governance alone can't see: by the time an MCP
tool call happens, the LLM call that requested it has already been
governed and returned. A database read with no row-level scope, or an
agent-to-agent delegation that inherits the parent's full permission set,
never passes through the LLM-call proxy at all — it only ever shows up
here, at the tool boundary.

Usage:
    agentmesh wrap --agent-id nightly-triage --team engineering \\
        --pii-mode mask --approval-tools "wire_transfer*,delete_*" \\
        -- python my_mcp_server.py

Programmatic (for testing / embedding):
    governor = MCPGovernor(pii_scanner=..., injection_detector=..., audit=...,
                           approval_gateway=..., allowed_tools=["read_*", "search_*"])
    decision = governor.evaluate_tool_call(tool_name="read_file", arguments={"path": "..."})
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MCPAction(str, Enum):
    ALLOW            = "allow"
    ALLOW_MASKED     = "allow_masked"
    BLOCK_INJECTION  = "block_injection"
    BLOCK_PII        = "block_pii"
    BLOCK_SCOPE      = "block_scope"
    PENDING_APPROVAL = "pending_approval"


@dataclass
class MCPDecision:
    action:      MCPAction
    arguments:   Dict[str, Any]              # possibly masked; original if blocked
    reason:      str          = ""
    pii_types:   List[str]    = field(default_factory=list)
    approval_id: str          = ""

    @property
    def forwardable(self) -> bool:
        return self.action in (MCPAction.ALLOW, MCPAction.ALLOW_MASKED)


class MCPGovernor:
    """
    Decides what happens to a single MCP `tools/call` request. Pure
    decision logic — no stdio, no subprocess — so it's unit-testable
    without spinning up a real MCP server.

    Args:
        agent_id:            identity attributed in the audit trail
        team:                team attribution for approval/scope rules
        allowed_tools:       glob patterns of tool names this agent may call
                             (empty = all tools allowed, subject to other checks)
        pii_scanner:         optional PIIScanner — masks/blocks sensitive arguments
        injection_detector:  optional InjectionDetector — blocks HIGH-risk arguments
        approval_gateway:    optional ApprovalGateway — reuses the same rule engine
                             the LLM-call proxy uses, matched against tool name here
        audit:               optional AuditTrail — every call is logged regardless
                             of the eventual decision
    """

    def __init__(
        self,
        agent_id: str = "mcp-agent",
        team: str = "",
        allowed_tools: Optional[List[str]] = None,
        pii_scanner: Optional[Any] = None,
        injection_detector: Optional[Any] = None,
        approval_gateway: Optional[Any] = None,
        audit: Optional[Any] = None,
    ):
        self.agent_id = agent_id
        self.team = team
        self.allowed_tools = allowed_tools or []
        self.pii_scanner = pii_scanner
        self.injection_detector = injection_detector
        self.approval_gateway = approval_gateway
        self.audit = audit

    def evaluate_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> MCPDecision:
        if self.audit:
            self.audit.record_tool_call(tool_name, self.agent_id, arguments)

        if self.allowed_tools and not any(fnmatch.fnmatch(tool_name, p) for p in self.allowed_tools):
            return MCPDecision(action=MCPAction.BLOCK_SCOPE, arguments=arguments,
                                reason=f"Tool '{tool_name}' is outside agent '{self.agent_id}'s scope")

        if self.injection_detector:
            from agentmesh.security.injection_detector import InjectionDetectedError
            blob = " ".join(str(v) for v in arguments.values())
            try:
                self.injection_detector.scan([{"role": "user", "content": blob}])
            except InjectionDetectedError as e:
                return MCPDecision(action=MCPAction.BLOCK_INJECTION, arguments=arguments,
                                    reason=f"Injection risk={e.result.risk_level.value} in tool arguments")

        masked_args = arguments
        pii_types: List[str] = []
        if self.pii_scanner:
            from agentmesh.security.pii_scanner import PIIDetectedError
            try:
                masked_args = {}
                for k, v in arguments.items():
                    if isinstance(v, str):
                        result = self.pii_scanner.scan(v)
                        masked_args[k] = result.cleaned
                        pii_types.extend(result.finding_types)
                    else:
                        masked_args[k] = v
            except PIIDetectedError as e:
                types = sorted({f.entity_type for f in e.findings})
                return MCPDecision(action=MCPAction.BLOCK_PII, arguments=arguments,
                                    reason=f"Sensitive data blocked in tool arguments: {', '.join(types)}",
                                    pii_types=types)

        if self.approval_gateway:
            decision = self.approval_gateway.evaluate(team=self.team, tool=tool_name, cost_usd=0.0, tokens=0)
            if decision.requires_approval:
                req = self.approval_gateway.request(
                    team=self.team, user="", tool=tool_name,
                    description=f"MCP tool call: {tool_name}({', '.join(masked_args.keys())})",
                    rule=decision.rule,
                )
                return MCPDecision(action=MCPAction.PENDING_APPROVAL, arguments=masked_args,
                                    reason=decision.reason, approval_id=req.id)

        action = MCPAction.ALLOW_MASKED if pii_types else MCPAction.ALLOW
        return MCPDecision(action=action, arguments=masked_args, pii_types=sorted(set(pii_types)))


class MCPGovernanceProxy:
    """
    Spawns the real MCP server as a subprocess and relays stdio JSON-RPC
    messages through an MCPGovernor. `tools/call` requests are intercepted
    and evaluated; every other message (initialize, tools/list,
    resources/*, prompts/*, notifications) passes through untouched.

    A blocked or pending-approval call is answered directly with a
    JSON-RPC error — it is never forwarded to the wrapped server.
    """

    def __init__(self, command: List[str], governor: MCPGovernor):
        self.command = command
        self.governor = governor
        self._proc: Optional[subprocess.Popen] = None

    def run(self) -> None:
        """Blocking: relays client stdin <-> child process <-> stdout until stdin closes."""
        self._proc = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        reader = threading.Thread(target=self._relay_child_to_stdout, daemon=True)
        reader.start()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_incoming(line)
            if response is not None:
                sys.stdout.write(response + "\n")
                sys.stdout.flush()

        self._proc.stdin.close()
        self._proc.wait()

    def handle_incoming(self, raw_line: str) -> Optional[str]:
        """
        Process one line of MCP JSON-RPC from the client.

        Returns a JSON-RPC response string to write directly to stdout when
        governance short-circuits the call, or None when the (possibly
        rewritten) message was forwarded to the child process — its
        response arrives asynchronously via `_relay_child_to_stdout`.
        """
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            self._forward(raw_line)
            return None

        if msg.get("method") != "tools/call":
            self._forward(raw_line)
            return None

        params = msg.get("params", {}) or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        decision = self.governor.evaluate_tool_call(tool_name, arguments)

        if decision.forwardable:
            forwarded = dict(msg)
            forwarded["params"] = {**params, "arguments": decision.arguments}
            self._forward(json.dumps(forwarded))
            return None

        return json.dumps({
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "error": {
                "code": -32000,
                "message": decision.reason,
                "data": {"action": decision.action.value, "approval_id": decision.approval_id},
            },
        })

    def _forward(self, line: str) -> None:
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _relay_child_to_stdout(self) -> None:
        for line in self._proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
