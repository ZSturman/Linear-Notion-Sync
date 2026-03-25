from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from src.config import Config, FieldMappings, LinearConfig, NotionConfig
from src.main import cli
from src.models.base import SyncSource
from src.models.task import UnifiedTask
from src.sync.engine import SyncEngine
from src.sync.reconciler import Change, ChangeType
from src.utils.state import SyncState, SyncStateStore


class FakeNotionClient:
    def __init__(self, updated_page: dict | None = None):
        self.updated_page = updated_page or {}
        self.updated_calls: list[tuple[str, dict]] = []

    def update_page(self, page_id: str, properties: dict) -> dict:
        self.updated_calls.append((page_id, properties))
        return self.updated_page


class FakeLinearClient:
    def __init__(self, workflow_states: dict[str, dict], updated_issue: dict | None = None):
        self.workflow_states = workflow_states
        self.updated_issue = updated_issue or {}
        self.updated_calls: list[tuple[str, dict]] = []

    def get_workflow_states(self) -> dict[str, dict]:
        return self.workflow_states

    def get_labels(self) -> dict[str, str]:
        return {}

    def update_issue(self, issue_id: str, **kwargs) -> dict:
        self.updated_calls.append((issue_id, kwargs))
        return self.updated_issue


def build_config(tmp_path) -> Config:
    return Config(
        notion=NotionConfig(
            token="token",
            projects_db_id="projects",
            milestones_db_id="milestones",
            tasks_db_id="tasks",
        ),
        linear=LinearConfig(api_key="key", team_id="team"),
        field_mappings=FieldMappings(),
        state_db_path=tmp_path / "sync_state.db",
    )


def test_task_status_aliases_match_linear_states() -> None:
    mappings = FieldMappings()

    assert mappings.task_statuses_match("ToDo", "Backlog", "backlog")
    assert mappings.task_statuses_match("In Review", "In Progress", "started")
    assert not mappings.task_statuses_match("Done", "In Progress", "started")


def test_status_command_uses_sync_state_fields(tmp_path, monkeypatch) -> None:
    config = build_config(tmp_path)
    state_store = SyncStateStore(config.state_db_path)
    state_store.upsert(
        SyncState(
            entity_type="task",
            notion_id="notion-page-1234",
            linear_id="linear-issue-5678",
            notion_last_modified=None,
            linear_last_modified=None,
            content_hash="abc123",
            last_synced=datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
        )
    )

    monkeypatch.setattr("src.main.load_config", lambda: config)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "tasks"])

    assert result.exit_code == 0
    assert "Total synced: 1" in result.output
    assert "notion:notion-p... <-> linear:linear-i..." in result.output


@pytest.mark.asyncio
async def test_task_update_to_linear_persists_fresh_linear_timestamp(tmp_path) -> None:
    config = build_config(tmp_path)
    state_store = SyncStateStore(config.state_db_path)
    notion = FakeNotionClient()
    linear = FakeLinearClient(
        workflow_states={
            "backlog-state": {"id": "backlog-state", "name": "Backlog", "type": "backlog"}
        },
        updated_issue={
            "id": "issue-1",
            "url": "https://linear.app/issue-1",
            "updatedAt": "2026-03-13T10:30:00Z",
            "state": {"id": "backlog-state", "name": "Backlog", "type": "backlog"},
        },
    )
    engine = SyncEngine(config, notion, linear, state_store)
    engine.relation_resolver.resolve_task_relations = lambda task, source: None

    task = UnifiedTask(
        notion_page_id="page-1",
        linear_id="issue-1",
        notion_last_modified=datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc),
        linear_last_modified=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        source=SyncSource.NOTION,
        title="Write docs",
        status="ToDo",
    )
    change = Change(
        change_type=ChangeType.UPDATE,
        entity_type="task",
        source=SyncSource.NOTION,
        target=SyncSource.LINEAR,
        record=task,
    )

    await engine._apply_task_change(change)

    assert linear.updated_calls == [
        ("issue-1", {"title": "Write docs", "stateId": "backlog-state"})
    ]
    assert task.linear_last_modified == datetime(2026, 3, 13, 10, 30, tzinfo=timezone.utc)
    assert task.linear_state_name == "Backlog"

    saved_state = state_store.get_by_notion_id("task", "page-1")
    assert saved_state is not None
    assert saved_state.linear_last_modified == datetime(
        2026, 3, 13, 10, 30, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_task_update_to_notion_persists_fresh_notion_timestamp(tmp_path) -> None:
    config = build_config(tmp_path)
    state_store = SyncStateStore(config.state_db_path)
    notion = FakeNotionClient(
        updated_page={
            "id": "page-1",
            "last_edited_time": "2026-03-13T11:15:00Z",
        }
    )
    linear = FakeLinearClient(workflow_states={})
    engine = SyncEngine(config, notion, linear, state_store)
    engine.relation_resolver.resolve_task_relations = lambda task, source: None

    task = UnifiedTask(
        notion_page_id="page-1",
        linear_id="issue-1",
        notion_last_modified=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        linear_last_modified=datetime(2026, 3, 13, 11, 0, tzinfo=timezone.utc),
        source=SyncSource.LINEAR,
        title="Write docs",
        status="In Progress",
        linear_state_name="In Progress",
        linear_state_type="started",
    )
    change = Change(
        change_type=ChangeType.UPDATE,
        entity_type="task",
        source=SyncSource.LINEAR,
        target=SyncSource.NOTION,
        record=task,
    )

    await engine._apply_task_change(change)

    assert notion.updated_calls
    assert task.notion_last_modified == datetime(2026, 3, 13, 11, 15, tzinfo=timezone.utc)

    saved_state = state_store.get_by_notion_id("task", "page-1")
    assert saved_state is not None
    assert saved_state.notion_last_modified == datetime(
        2026, 3, 13, 11, 15, tzinfo=timezone.utc
    )