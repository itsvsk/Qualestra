from __future__ import annotations

import os

from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.blackboard.port import Blackboard
from sentinelqa.blackboard.postgres import PostgresBlackboard
from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.supervisor import DeterministicSupervisor
from sentinelqa.storage import build_artifact_store_from_env
from sentinelqa.storage.database import Database

database_url = os.getenv("DATABASE_URL")
database = Database(database_url) if database_url else None
blackboard: Blackboard = (
    PostgresBlackboard(database.sessions) if database is not None else InMemoryBlackboard()
)
registry = AgentRegistry.from_file("config/agents.yaml")
supervisor = DeterministicSupervisor(
    registry,
    blackboard,
    application_directory=os.getenv("SENTINEL_APPLICATION_DIRECTORY", "config/applications"),
)
artifacts = build_artifact_store_from_env()
