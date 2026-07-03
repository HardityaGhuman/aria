"""core/agents/specialist.py
--------------------------
Value objects for the hierarchical agent tree. A Specialist is the Unit-1
run_agent_loop parameterised with a SCOPED tool registry and an RBAC floor; the
supervisor delegates to exactly one. SpecialistResult is the structured contract
handed back (typed fields, not stringly feedback) so chat_service fuses the tool
note without re-parsing prose."""
from dataclasses import dataclass, field

from backend.core.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Specialist:
    name: str
    description: str
    min_role: str
    registry: ToolRegistry
    uses_tools: bool


@dataclass
class SpecialistResult:
    specialist: str
    tool_results: list[dict] = field(default_factory=list)
    tool_note: str | None = None
    # no_tools | ok | gather_failed
    status: str = "no_tools"
