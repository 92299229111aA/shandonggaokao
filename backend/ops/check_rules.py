"""Dev self-check for the Rule Engine wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rules.engine import DEFAULT_RULES
from app.rules.service import RuleEngine, RuleEngineConfig

engine = RuleEngine(config=RuleEngineConfig())
print("rules type:", type(engine.rules))
print("rules value:", repr(engine.rules)[:120])
print("DEFAULT_RULES:", type(DEFAULT_RULES), len(DEFAULT_RULES))
print("codes:", [rule.code for rule in DEFAULT_RULES])
assert isinstance(engine.rules, tuple), f"self.rules is {type(engine.rules)}"
assert all(hasattr(rule, "code") for rule in engine.rules)
print("OK")
