"""services/write_agents.py
------------------------
The agent registry: the one place that knows "leave" means THIS spec, THIS compiled graph,
and THIS replay function. The cross-agent routes (/agents/cases, /admin/write/*) are
agent-agnostic because of it — they take an agent NAME and look it up here instead of
importing three graphs and branching on strings.

Populated at startup (main.py), after the graphs are compiled against the shared Postgres
checkpointer. An agent whose kill switch is off is simply never registered, so its Cases
are invisible to the cross-agent surfaces — which is what "off" should mean."""
from collections.abc import Callable
from dataclasses import dataclass

from backend.core.write.case_store import CaseSpec


@dataclass(frozen=True)
class WriteAgent:
    name: str
    spec: CaseSpec
    graph: object
    replay: Callable                  # replay(graph, *, case_id, actor_id) -> dict
    resume: Callable | None = None    # resume(graph, *, case_id, decision, actor_id) -> dict


AGENTS: dict[str, WriteAgent] = {}


def register(agent: WriteAgent) -> None:
    AGENTS[agent.name] = agent


def get(name: str) -> WriteAgent | None:
    return AGENTS.get(name)


def specs() -> list[CaseSpec]:
    return [a.spec for a in AGENTS.values()]


def reset() -> None:
    """Tests only: the registry is process-global module state."""
    AGENTS.clear()
