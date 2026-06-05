"""BPMN 2.0 to LangGraph bridge — migrate legacy workflows to agentic AI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

# Task types that are safe to convert to agent nodes
AGENT_SAFE_TASK_TYPES = {
    "userTask",
    "serviceTask",
    "scriptTask",
    "businessRuleTask",
}

# Task name patterns that should remain deterministic
DETERMINISTIC_PATTERNS = [
    "calculat", "comput", "validat", "check", "verify",
    "compliance", "regulatory", "format", "transform",
]


@dataclass
class BPMNTask:
    id: str
    name: str
    task_type: str
    is_agent_safe: bool
    is_deterministic: bool
    reason: str


@dataclass
class MigrationResult:
    tasks: List[BPMNTask] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def generate_langgraph(self) -> str:
        """Generate Python code for the equivalent LangGraph graph."""
        lines = [
            "from langgraph.graph import StateGraph, END",
            "from typing import TypedDict, Annotated",
            "import operator",
            "",
            "class WorkflowState(TypedDict):",
            "    messages: Annotated[list, operator.add]",
            "    context: dict",
            "",
            "graph = StateGraph(WorkflowState)",
            "",
        ]

        for task in self.tasks:
            node_name = task.name.lower().replace(" ", "_")
            if task.is_deterministic:
                lines.append(f"# DETERMINISTIC: {task.name} — keep as Python function")
                lines.append(f"def {node_name}(state: WorkflowState) -> WorkflowState:")
                lines.append(f'    """Migrated from BPMN task: {task.name}"""')
                lines.append("    # TODO: implement deterministic logic")
                lines.append("    return state")
            else:
                lines.append(f"# AGENT NODE: {task.name}")
                lines.append(f"def {node_name}(state: WorkflowState) -> WorkflowState:")
                lines.append(f'    """Agent node migrated from: {task.name}"""')
                lines.append("    # TODO: implement agent logic (LLM call)")
                lines.append("    return state")
            lines.append(f"graph.add_node('{node_name}', {node_name})")
            lines.append("")

        for edge in self.edges:
            src = edge["source"].lower().replace(" ", "_")
            tgt = edge["target"].lower().replace(" ", "_")
            lines.append(f"graph.add_edge('{src}', '{tgt}')")

        lines.extend(["", "compiled = graph.compile()"])
        return "\n".join(lines)

    def report(self) -> str:
        """Return a human-readable migration report."""
        agent_count = sum(1 for t in self.tasks if not t.is_deterministic)
        det_count = sum(1 for t in self.tasks if t.is_deterministic)
        lines = [
            "=== AgentMesh BPMN Migration Report ===",
            f"Total tasks: {len(self.tasks)}",
            f"  Agent nodes (non-deterministic): {agent_count}",
            f"  Deterministic nodes (unchanged): {det_count}",
            "",
            "Task Analysis:",
        ]
        for task in self.tasks:
            status = "AGENT" if not task.is_deterministic else "DETERMINISTIC"
            lines.append(f"  [{status}] {task.name}: {task.reason}")
        if self.warnings:
            lines.append("\nWarnings:")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


class BPMNBridge:
    """
    Converts BPMN 2.0 process definitions (Camunda/Activiti/jBPM XML)
    into equivalent LangGraph graphs, preserving governance semantics.

    Identifies which tasks are safe to convert to agent nodes vs.
    which must remain deterministic for compliance reasons.
    """

    def migrate(self, bpmn_path: str) -> MigrationResult:
        """Parse a BPMN 2.0 XML file and generate a migration plan."""
        try:
            tree = ET.parse(bpmn_path)
            root = tree.getroot()
        except Exception as e:
            raise ValueError(f"Failed to parse BPMN file: {e}")

        result = MigrationResult()

        # Parse tasks
        for task_type in AGENT_SAFE_TASK_TYPES:
            for elem in root.iter(f"{{{BPMN_NS}}}{task_type}"):
                task = self._analyze_task(elem, task_type)
                result.tasks.append(task)

        # Parse sequence flows (edges)
        for flow in root.iter(f"{{{BPMN_NS}}}sequenceFlow"):
            result.edges.append({
                "id": flow.get("id", ""),
                "source": flow.get("sourceRef", ""),
                "target": flow.get("targetRef", ""),
            })

        if not result.tasks:
            result.warnings.append(
                "No tasks found. Verify the BPMN namespace matches the file format."
            )

        logger.info("BPMN migration: %d tasks analyzed", len(result.tasks))
        return result

    def _analyze_task(self, elem: ET.Element, task_type: str) -> BPMNTask:
        task_id = elem.get("id", "")
        name = elem.get("name", task_id)
        name_lower = name.lower()

        is_deterministic = any(pat in name_lower for pat in DETERMINISTIC_PATTERNS)
        is_agent_safe = not is_deterministic

        if is_deterministic:
            reason = "Name suggests rule-based or computational logic — keep deterministic"
        else:
            reason = "Candidate for agent-ification — involves judgment or document processing"

        return BPMNTask(
            id=task_id,
            name=name,
            task_type=task_type,
            is_agent_safe=is_agent_safe,
            is_deterministic=is_deterministic,
            reason=reason,
        )
