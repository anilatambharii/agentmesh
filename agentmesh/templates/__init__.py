"""Pre-built governance policy templates for common enterprise verticals."""

import importlib.resources
from pathlib import Path
from typing import Dict

TEMPLATE_DIR = Path(__file__).parent
_HEADER_MARKER = "AgentMesh Policy Template:"


def load_template(name: str) -> str:
    """
    Load a built-in policy template by name.

    Available templates:
        fintech             — SOX + PCI-DSS for financial AI agents
        healthcare          — HIPAA + EU AI Act for healthcare agents
        enterprise          — Standard enterprise governance baseline
        research            — High-autonomy research agents with cost controls
        customer_service    — Contact center AI with strict PII controls
        nvidia_nim          — Optimized for NVIDIA NIM model deployments
        eu_ai_act_high_risk — Article 6/Annex III high-risk systems (scoring, screening)

    List everything actually bundled with `list_templates()` — this
    docstring can drift as templates are added.

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


def list_templates() -> Dict[str, str]:
    """
    Return {template_name: title} for every bundled template, so callers
    (the `agentmesh policy list-packs` CLI, or anyone building a community
    registry on top of this) can discover them without hardcoding names.
    Title is parsed from each file's leading "AgentMesh Policy Template:
    <title>" comment line.
    """
    templates: Dict[str, str] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        title = path.stem
        lines = path.read_text().splitlines()
        if lines and _HEADER_MARKER in lines[0]:
            title = lines[0].split(_HEADER_MARKER, 1)[1].strip()
        templates[path.stem] = title
    return templates


__all__ = ["load_template", "list_templates", "TEMPLATE_DIR"]
