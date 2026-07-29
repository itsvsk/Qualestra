from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from sentinelqa.domain.models import Budget


class BudgetExhausted(RuntimeError):
    pass


@dataclass(slots=True)
class BudgetSnapshot:
    tool_calls: int = 0
    model_tokens: int = 0
    api_requests: int = 0
    artifact_bytes: int = 0


class AtomicBudget:
    def __init__(self, limits: Budget) -> None:
        self.limits = limits
        self.used = BudgetSnapshot()
        self._lock = asyncio.Lock()

    async def consume(self, kind: str, amount: int = 1) -> None:
        if amount < 0 or kind not in BudgetSnapshot.__dataclass_fields__:
            raise ValueError("invalid budget consumption")
        limit_name = f"max_{kind}"
        async with self._lock:
            next_value = getattr(self.used, kind) + amount
            if next_value > getattr(self.limits, limit_name):
                raise BudgetExhausted(f"{kind} budget exhausted")
            setattr(self.used, kind, next_value)

    @staticmethod
    def subdivide(total: int, weights: list[float]) -> list[int]:
        if total < 0 or not weights or any(weight < 0 for weight in weights):
            raise ValueError("invalid budget subdivision")
        decimal_weights = [Decimal(str(weight)) for weight in weights]
        weight_sum = sum(decimal_weights)
        if weight_sum == 0:
            raise ValueError("weights cannot sum to zero")
        raw = [Decimal(total) * weight / weight_sum for weight in decimal_weights]
        allocated = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in raw]
        remainders = sorted(
            range(len(raw)), key=lambda index: (-(raw[index] - allocated[index]), index)
        )
        for index in remainders[: total - sum(allocated)]:
            allocated[index] += 1
        return allocated
