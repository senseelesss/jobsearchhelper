"""Durable conservative reservations around the existing scoring provider.

A failed/unknown request keeps its reservation. No retries occur outside the
provider's explicitly bounded SDK retry setting. Prices are operator configuration,
not a promise about the provider's invoice; use a provider account cap as well.
"""

import math
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo


class BudgetExceeded(RuntimeError):
    pass


def request_ceiling(settings) -> float:
    if not (
        1 <= settings.openai_timeout_seconds <= 120
        and 0 <= settings.openai_max_retries <= 2
        and 256 <= settings.openai_max_output_tokens <= 4096
        and 1024 <= settings.openai_max_input_bytes <= 100000
        and settings.openai_model.strip()
    ):
        raise ValueError("Invalid AI request bounds")
    rates = (settings.openai_input_cost_per_million, settings.openai_output_cost_per_million)
    if any(not math.isfinite(rate) or rate <= 0 for rate in rates):
        raise ValueError("Positive finite AI prices required for budgeted scoring")
    # UTF-8 payload bytes upper-bound ordinary text tokens. Reserve additional
    # system/schema framing, maximum output, and every possible SDK retry.
    return (
        (
            (settings.openai_max_input_bytes + 32768) * rates[0]
            + settings.openai_max_output_tokens * rates[1]
        )
        / 1_000_000
        * (settings.openai_max_retries + 1)
    )


class BudgetedProvider:
    def __init__(self, provider, database, settings, run_cap: float, day_cap: float):
        if any(not math.isfinite(n) or n <= 0 for n in (run_cap, day_cap)):
            raise ValueError("Positive finite run and daily AI budgets required")
        self.provider, self.database = provider, database
        self.model = provider.model
        self.run_cap, self.day_cap = run_cap, day_cap
        self.ceiling = request_ceiling(settings)
        self.run_key = uuid.uuid4().hex

    def score(self, profile, vacancy, provisional):
        day = datetime.now(ZoneInfo("Europe/Vienna")).date().isoformat()
        with closing(sqlite3.connect(self.database, timeout=5)) as connection, connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            total, run = connection.execute(
                "SELECT (SELECT COALESCE(SUM(reserved_usd),0) FROM ai_budget_reservations WHERE day=?), "
                "(SELECT COALESCE(SUM(reserved_usd),0) FROM ai_budget_reservations WHERE run_key=?)",
                (day, self.run_key),
            ).fetchone()
            if total + self.ceiling > self.day_cap or run + self.ceiling > self.run_cap:
                raise BudgetExceeded("AI reservation budget exhausted")
            connection.execute(
                "INSERT INTO ai_budget_reservations(day,run_key,reserved_usd) VALUES (?,?,?)",
                (day, self.run_key, self.ceiling),
            )
        # Keep the conservative reservation even on failure, interruption or missing usage.
        return self.provider.score(profile, vacancy, provisional)
