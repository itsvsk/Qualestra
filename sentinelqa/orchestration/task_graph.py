from __future__ import annotations

from collections import defaultdict, deque
from uuid import UUID

from sentinelqa.domain.models import AgentTask


class TaskGraphError(ValueError):
    pass


class TaskGraph:
    def __init__(self, tasks: list[AgentTask]) -> None:
        self.tasks = {task.task_id: task for task in tasks}
        if len(self.tasks) != len(tasks):
            raise TaskGraphError("duplicate task ID")
        self._validate()

    def _validate(self) -> None:
        indegree: dict[UUID, int] = {task_id: 0 for task_id in self.tasks}
        children: dict[UUID, list[UUID]] = defaultdict(list)
        for task in self.tasks.values():
            for dependency in task.dependency_task_ids:
                if dependency not in self.tasks:
                    raise TaskGraphError(f"missing dependency: {dependency}")
                indegree[task.task_id] += 1
                children[dependency].append(task.task_id)
        queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(self.tasks):
            raise TaskGraphError("task dependencies contain a cycle")

    def ready(self, succeeded: set[UUID], active: set[UUID]) -> list[AgentTask]:
        return sorted(
            [
                task
                for task in self.tasks.values()
                if task.task_id not in succeeded | active
                and set(task.dependency_task_ids).issubset(succeeded)
            ],
            key=lambda task: str(task.task_id),
        )
