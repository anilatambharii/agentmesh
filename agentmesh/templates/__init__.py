"""Pre-built governance policy templates for common enterprise verticals."""

import importlib.resources
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent


def load_template(name: str) -> str:
    """
    Load a built-in policy template by name.

    Available templates:
        fintech         — SOX + PCI-DSS for financial AI agents
        healthcare      — HIPAA + EU AI Act for healthcare agents
        enterprise      — Standard enterprise governance baseline
        research        — High-autonomy research agents with cost controls
        customer_service — Contact center AI with strict PII controls
        nvidia_nim      — Optimized for NVIDIA NIM model deployments

    Example:
        from agentmesh.templates import load_template
        from agentmesh.policy.engine import Policy

        policy = Policy.from_yaml(load_template("fintech"))
    """
    path = TEMPLATE_DIR / f"{name}.yaml"
    if not path.exists():
        available = [p.stem for p in TEMPLATE_DIR.glob("*.yaml")]
        raise FileNotFoundError(
            f"Template {name!r} not found. Available: {available}"
        )
    return path.read_text()


__all__ = ["load_template", "TEMPLATE_DIR"]
