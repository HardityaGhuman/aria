"""backend/core/control
----------------------
The deterministic read-pipeline control layer (rescope §4).

Every request is turned into a typed, versioned plan, validated against
server-owned budgets, executed by exactly one specialist, validated again, and
recorded as a redacted boundary trace. No LLM chooses the plan, the budgets, or
the recovery action — those live here in Python. See `models` for the vocabulary.
"""
