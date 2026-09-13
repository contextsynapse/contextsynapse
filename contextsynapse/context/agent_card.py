"""
Agent Card (A2A Protocol)
=========================
Implements Google's Agent-to-Agent (A2A) protocol Agent Card spec.
Each agent publishes a card describing its identity, capabilities,
skills, and availability. The lead agent queries cards before
assigning tasks.

Spec reference: https://google.github.io/A2A/#/documentation

Agent Card JSON structure:
{
  "name": "...",
  "description": "...",
  "url": "...",
  "provider": { "organization": "...", "url": "..." },
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": false
  },
  "authentication": { "schemes": ["apiKey"] },
  "defaultInputModes": ["text"],
  "defaultOutputModes": ["text"],
  "skills": [
    {
      "id": "...",
      "name": "...",
      "description": "...",
      "tags": ["..."],
      "examples": ["..."]
    }
  ]
}
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentSkill:
    """A specific skill an agent can perform."""
    id: str
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "examples": self.examples,
        }


@dataclass
class AgentCard:
    """A2A-compatible Agent Card."""
    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    provider: Dict[str, str] = field(default_factory=dict)
    capabilities: Dict[str, bool] = field(default_factory=lambda: {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    })
    authentication: Dict[str, Any] = field(default_factory=lambda: {
        "schemes": ["apiKey"],
    })
    default_input_modes: List[str] = field(default_factory=lambda: ["text"])
    default_output_modes: List[str] = field(default_factory=lambda: ["text"])
    skills: List[AgentSkill] = field(default_factory=list)

    # Runtime fields (not in A2A spec but needed for orchestration)
    agent_id: str = ""
    platform: str = "app"
    status: str = "active"          # active | inactive | busy
    last_seen: Optional[float] = None
    adapter: str = "openai"         # openai | anthropic | groq | ollama

    def is_available(self, timeout_seconds: int = 300) -> bool:
        """Check if agent is available (active + seen recently)."""
        if self.status != "active":
            return False
        if self.last_seen is None:
            return True  # never checked in, assume available
        return (time.time() - self.last_seen) < timeout_seconds

    def can_do(self, skill_id: str) -> bool:
        """Check if agent has a specific skill."""
        return any(s.id == skill_id for s in self.skills)

    def has_tag(self, tag: str) -> bool:
        """Check if any skill has a specific tag."""
        return any(tag in s.tags for s in self.skills)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to A2A-compatible JSON."""
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "provider": self.provider,
            "capabilities": self.capabilities,
            "authentication": self.authentication,
            "defaultInputModes": self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
            "skills": [s.to_dict() for s in self.skills],
            # Runtime extensions
            "agentId": self.agent_id,
            "platform": self.platform,
            "status": self.status,
            "lastSeen": self.last_seen,
            "adapter": self.adapter,
            "available": self.is_available(),
        }

    def to_prompt_summary(self) -> str:
        """One-line summary for injecting into LLM prompts."""
        skill_list = ", ".join(s.name for s in self.skills) if self.skills else "general"
        avail = "available" if self.is_available() else "INACTIVE"
        return f"{self.name} ({avail}, {self.platform}, skills: {skill_list})"

    @classmethod
    def from_registry_agent(cls, agent, metadata: Optional[Dict] = None) -> "AgentCard":
        """Build an AgentCard from a registry Agent object."""
        meta = metadata or getattr(agent, "metadata", {}) or {}
        adapter = meta.get("adapter", "openai")
        skills_raw = meta.get("skills", [])

        skills = []
        for s in skills_raw:
            if isinstance(s, dict):
                skills.append(AgentSkill(
                    id=s.get("id", s.get("name", "unknown")),
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    tags=s.get("tags", []),
                    examples=s.get("examples", []),
                ))
            elif isinstance(s, str):
                skills.append(AgentSkill(id=s, name=s, description=s))

        # Infer skills from capabilities if none provided
        caps = getattr(agent, "capabilities", []) or []
        if not skills and caps:
            for cap in caps:
                skills.append(AgentSkill(id=cap, name=cap, description=f"Can {cap}"))

        last_seen = getattr(agent, "last_seen", None)
        if isinstance(last_seen, str):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                last_seen = dt.timestamp()
            except Exception:
                last_seen = None

        return cls(
            name=agent.name,
            description=meta.get("description", f"{agent.name} agent"),
            agent_id=agent.agent_id,
            platform=getattr(agent, "platform", "app"),
            status=getattr(agent, "status", "active"),
            last_seen=last_seen,
            adapter=adapter,
            skills=skills,
            provider={"organization": meta.get("organization", "")},
        )


def build_roster_prompt(cards: List[AgentCard], lead_name: str) -> str:
    """Build the agent roster section for the lead agent's system prompt."""
    lines = ["Available agents:"]
    for card in cards:
        role = "LEAD" if card.name == lead_name else "worker"
        status = "AVAILABLE" if card.is_available() else "INACTIVE"
        skills = ", ".join(s.name for s in card.skills) if card.skills else "general"
        lines.append(
            f"  - {card.name} [{role}] ({status}, platform={card.platform}, "
            f"adapter={card.adapter}, skills=[{skills}])"
        )

    lines.append("")
    lines.append("Rules for task assignment:")
    lines.append("  - Only assign tasks to AVAILABLE agents")
    lines.append("  - Match tasks to agent skills when possible")
    lines.append("  - If an agent is INACTIVE, assign their work to yourself or another available agent")
    lines.append("  - Set assigned_to to the exact agent name")
    return "\n".join(lines)
