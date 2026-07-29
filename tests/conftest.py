from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sentinelqa.domain.enums import AgentType
from sentinelqa.domain.models import AgentTask, Budget


@pytest.fixture
def run_id():
    return uuid4()


@pytest.fixture
def task_factory(run_id):
    def make(agent_type: AgentType = AgentType.CHANGE_IMPACT, **updates):
        defaults = {
            "run_id": run_id,
            "agent_type": agent_type,
            "input_ref": "blackboard://context/input",
            "idempotency_key": AgentTask.stable_key(run_id, agent_type),
            "target_partition": "atlas:read-only",
            "budget": Budget(),
            "deadline": datetime.now(UTC) + timedelta(minutes=5),
        }
        defaults.update(updates)
        return AgentTask(**defaults)

    return make
