"""
Cost attribution and chargeback — enterprise-grade AI spend management.

Track AI agent costs per team, project, workflow, and user. Generate
internal chargeback reports compatible with Stripe, internal billing
systems, or FinOps tooling.

This is the feature that makes CFOs say yes to production AI agents.

Example:
    attributor = CostAttributor()
    attributor.record(team="data-science", project="fraud-detection",
                      model="claude-haiku-4-5", tokens=12_500, cost_usd=0.01)
    report = attributor.summary(group_by="team")
    print(report.to_csv())
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UsageRecord:
    """A single AI agent invocation cost record."""
    timestamp: float = field(default_factory=time.time)
    team: str = "default"
    project: str = "default"
    workflow: str = ""
    agent_id: str = ""
    user_id: str = ""
    model: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tags: Dict[str, str] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TeamUsageSummary:
    """Aggregated cost summary for a group (team, project, etc.)."""
    group_key: str
    group_by: str
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0
    unique_models: List[str] = field(default_factory=list)
    records: List[UsageRecord] = field(default_factory=list)

    @property
    def avg_cost_per_call(self) -> float:
        return self.total_cost_usd / self.call_count if self.call_count else 0.0

    @property
    def avg_tokens_per_call(self) -> float:
        return self.total_tokens / self.call_count if self.call_count else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_key": self.group_key,
            "group_by": self.group_by,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "call_count": self.call_count,
            "avg_cost_per_call": round(self.avg_cost_per_call, 6),
            "avg_tokens_per_call": round(self.avg_tokens_per_call, 1),
            "unique_models": self.unique_models,
        }


class UsageSummaryCollection:
    """A collection of usage summaries with export capabilities."""

    def __init__(self, summaries: List[TeamUsageSummary], group_by: str):
        self.summaries = summaries
        self.group_by = group_by

    def to_json(self, indent: int = 2) -> str:
        return json.dumps([s.to_dict() for s in self.summaries], indent=indent)

    def to_csv(self) -> str:
        if not self.summaries:
            return ""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(self.summaries[0].to_dict().keys()))
        writer.writeheader()
        for s in self.summaries:
            row = s.to_dict()
            row["unique_models"] = "|".join(row["unique_models"])
            writer.writerow(row)
        return buf.getvalue()

    def to_dict(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.summaries]

    def total_cost_usd(self) -> float:
        return sum(s.total_cost_usd for s in self.summaries)

    def __repr__(self) -> str:
        total = self.total_cost_usd()
        return f"<UsageSummaryCollection group_by={self.group_by!r} groups={len(self.summaries)} total=${total:.4f}>"


class CostAttributor:
    """
    Record and attribute AI agent costs to teams, projects, and users.

    Enables:
    - Internal chargeback (which team owns this AI spend?)
    - FinOps cost optimization (which workflow is most expensive?)
    - Budget accountability (which project is over quota?)
    - Executive reporting (total AI spend by department)

    Thread-safe for concurrent agent deployments.
    """

    def __init__(self):
        self._records: List[UsageRecord] = []

    def record(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        team: str = "default",
        project: str = "default",
        workflow: str = "",
        agent_id: str = "",
        user_id: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> UsageRecord:
        """Record a single LLM call's cost attribution."""
        rec = UsageRecord(
            team=team,
            project=project,
            workflow=workflow,
            agent_id=agent_id,
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            tags=tags or {},
        )
        self._records.append(rec)
        return rec

    def record_from_mesh_stats(
        self,
        stats: Dict[str, Any],
        team: str = "default",
        project: str = "default",
        **kwargs,
    ) -> UsageRecord:
        """Convenience: record from an AgentMesh.stats dict."""
        return self.record(
            model=kwargs.get("model", "unknown"),
            input_tokens=stats.get("tokens_used", 0),
            cost_usd=stats.get("cost_usd", 0.0),
            team=team,
            project=project,
            **{k: v for k, v in kwargs.items() if k != "model"},
        )

    def summary(
        self,
        group_by: str = "team",
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> UsageSummaryCollection:
        """
        Aggregate usage records by the given dimension.

        Args:
            group_by: One of "team", "project", "workflow", "user_id", "model"
            since: Unix timestamp lower bound (inclusive)
            until: Unix timestamp upper bound (inclusive)
        """
        records = self._filter_records(since, until)
        groups: Dict[str, TeamUsageSummary] = {}

        for rec in records:
            key = getattr(rec, group_by, "unknown")
            if key not in groups:
                groups[key] = TeamUsageSummary(group_key=key, group_by=group_by)
            s = groups[key]
            s.records.append(rec)
            s.total_tokens += rec.total_tokens
            s.total_input_tokens += rec.input_tokens
            s.total_output_tokens += rec.output_tokens
            s.total_cost_usd += rec.cost_usd
            s.call_count += 1
            if rec.model not in s.unique_models:
                s.unique_models.append(rec.model)

        summaries = sorted(groups.values(), key=lambda x: x.total_cost_usd, reverse=True)
        return UsageSummaryCollection(summaries, group_by)

    def top_spenders(self, n: int = 10, group_by: str = "team") -> List[TeamUsageSummary]:
        """Return the top N spenders for a given dimension."""
        return self.summary(group_by=group_by).summaries[:n]

    def budget_status(
        self,
        budgets: Dict[str, float],
        group_by: str = "team",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compare current spend against budgets.

        Args:
            budgets: {team_name: monthly_budget_usd}

        Returns:
            {team_name: {spent, budget, remaining, pct_used, over_budget}}
        """
        col = self.summary(group_by=group_by)
        status = {}
        for s in col.summaries:
            budget = budgets.get(s.group_key, float("inf"))
            status[s.group_key] = {
                "spent_usd": round(s.total_cost_usd, 4),
                "budget_usd": budget,
                "remaining_usd": round(max(0, budget - s.total_cost_usd), 4),
                "pct_used": round(s.total_cost_usd / budget * 100, 1) if budget != float("inf") else None,
                "over_budget": s.total_cost_usd > budget,
            }
        return status

    def export_json(self, path: str) -> None:
        """Export all raw records to a JSON file."""
        import json as _json
        from dataclasses import asdict
        with open(path, "w") as f:
            _json.dump([asdict(r) for r in self._records], f, indent=2)

    def export_csv(self, path: str) -> None:
        """Export all raw records to a CSV file."""
        if not self._records:
            return
        from dataclasses import asdict
        rows = [asdict(r) for r in self._records]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def _filter_records(
        self,
        since: Optional[float],
        until: Optional[float],
    ) -> List[UsageRecord]:
        records = self._records
        if since:
            records = [r for r in records if r.timestamp >= since]
        if until:
            records = [r for r in records if r.timestamp <= until]
        return records

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self._records)

    @property
    def total_calls(self) -> int:
        return len(self._records)
