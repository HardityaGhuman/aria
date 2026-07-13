"""core/write/
-----------
Agent-agnostic write-boundary reliability. This package knows NOTHING about
onboarding, leave, or jira — it is the shared spine every write Case is meant to
adopt (Leave and Jira get retrofitted in a follow-up slice).

Three concerns, kept separate:
  errors.py   detect + classify  (transient vs permanent; unknown => permanent)
  breaker.py  prevent repeated harm (halt automation on a flapping connector)
  trace.py    observe (system-owned, redacted, per-node + per-write-attempt)

The retry policy itself is NOT here: it lives in each graph as a conditional edge
back into the write node, so every attempt is its own checkpoint."""
