# Linear-Notion Sync

Bidirectional sync between Notion and Linear for projects, milestones, and tasks, with an optional set of self-hosted n8n workflow exports for scheduled automation.

This repository contains two related pieces:

- A Python CLI that performs the actual synchronization logic.
- Optional n8n workflow exports that can run the sync on a schedule or from a Notion trigger.

## What This Project Solves

If you plan work in Notion but execute work in Linear, this project keeps the two systems aligned without treating one of them as read-only.

It supports:

- Bidirectional sync between Notion and Linear.
- Projects, project milestones, and tasks/issues.
- Relationship preservation across projects, milestones, and parent/subtask trees.
- Conflict handling when both sides changed.
- Persistent sync state so existing records are matched instead of duplicated.
- Selective sync from Notion using the `linear sync` checkbox.
- Dry-run support for safe testing.

## Repository Contents

```text
src/                     Python sync service and CLI
tests/                   Automated tests
n8n/workflows/           Optional n8n workflow exports
.env.example             Sample configuration
requirements.txt         Python dependencies
```

## Requirements

- Python 3.10+
- A Notion integration with access to your three target databases
- A Linear API key
- A Linear team that the authenticated user can access
- Optional: a self-hosted n8n instance with the Execute Command node enabled

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/ZSturman/Linear-Notion-Sync.git
cd Linear-Notion-Sync
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv
source venv/bin/activate
```

On Windows use `venv\Scripts\activate`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create your local environment file

```bash
cp .env.example .env
```

Then fill in the required values in `.env`.

### 5. Validate your configuration

```bash
python -m src.main validate
```

This validates that required environment variables are present and that the clients can be initialized. It is a setup sanity check, not a full end-to-end sync test.

### 6. Run a dry run first

```bash
python -m src.main sync --dry-run
```

### 7. Run a real sync

```bash
python -m src.main sync
```

## Environment Variables

The CLI and the n8n workflows share most configuration values.

| Variable | CLI | n8n | Description |
|----------|-----|-----|-------------|
| `NOTION_TOKEN` | Required | Required | Notion integration token |
| `NOTION_PROJECTS_DB_ID` | Required | Required | Notion projects database ID |
| `NOTION_MILESTONES_DB_ID` | Required | Required | Notion milestones database ID |
| `NOTION_TASKS_DB_ID` | Required | Required | Notion tasks database ID |
| `LINEAR_API_KEY` | Required | Required | Linear API key |
| `LINEAR_TEAM_ID` | Optional | Optional | Explicit Linear team ID |
| `LINEAR_TEAM_KEY` | Optional | Optional | Human-friendly Linear team key such as `ENG` or `PROJ` |
| `SYNC_STATE_PATH` | Optional | No | SQLite state file path for the Python CLI, default `./sync_state.db` |
| `NOTION_AUTOMATION_LOG_DB_ID` | No | Required for exported workflows | Notion database used for n8n run logging |
| `N8N_SYNC_STATE_PATH` | No | Optional | Shared JSON state file path for the n8n exports |
| `LOG_LEVEL` | Optional | No | Logging level for the Python CLI |
| `DRY_RUN` | Optional | No | Default dry-run mode for the Python CLI |
| `CONFLICT_STRATEGY` | Optional | Optional | `last-write-wins`, `notion-primary`, or `linear-primary` |
| `IMPORT_LINEAR_SYNC_DEFAULT` | Optional | No | When creating Notion pages from Linear, default `linear sync` to true |

### Team Selection

You can provide either `LINEAR_TEAM_ID` or `LINEAR_TEAM_KEY`.

- If `LINEAR_TEAM_ID` is set, it is used directly.
- If `LINEAR_TEAM_ID` is empty and `LINEAR_TEAM_KEY` is set, the client resolves the team ID from the key.
- If both are empty, the client falls back to the first available team returned by Linear.

## Notion Database Schema

The property names below are currently hardcoded in the Python service and the exported n8n workflows. The names need to match exactly unless you also change the code.

### Projects database

| Property | Type | Purpose |
|----------|------|---------|
| `title` | Title | Project name |
| `linear id` | Rich Text | Linked Linear project ID |
| `linear sync` | Checkbox | Include this record in sync |
| `status` | Status | Project state |
| `one liner` | Rich Text | Project description |
| `started at` | Date | Project start date |
| `milestones` | Relation | Optional relation used by your workspace |
| `tasks` | Relation | Optional relation used by your workspace |

Supported project status mapping:

| Notion | Linear |
|--------|--------|
| `Predevelopment` | `planned` |
| `Active` | `started` |
| `Dormant` | `paused` |
| `Complete` | `completed` |
| `Abandoned` | `canceled` |

### Milestones database

| Property | Type | Purpose |
|----------|------|---------|
| `milestone` | Title | Milestone name |
| `linear id` | Rich Text | Linked Linear milestone ID |
| `linear sync` | Checkbox | Include this record in sync |
| `description` | Rich Text | Milestone description |
| `project` | Relation | Linked project |
| `tasks` | Relation | Optional relation used by your workspace |
| `due date (manual)` | Date | Writable due date field |
| `effective due date` | Formula | Read-side due date field |
| `all tasks complete?` | Formula or Checkbox | Optional completion helper |

### Tasks database

| Property | Type | Purpose |
|----------|------|---------|
| `task` | Title | Task title |
| `linear id` | Rich Text | Linked Linear issue ID |
| `linear sync` | Checkbox | Include this record in sync |
| `complete` | Checkbox | Completion flag |
| `status` | Select | Workflow status |
| `project` | Relation | Linked project |
| `milestones` | Relation | Linked milestone |
| `parent task` | Relation | Parent task for subtasks |
| `subtasks` | Relation | Optional reverse relation |
| `priority` | Select | Priority |
| `due date` | Date | Due date |
| `type` | Select | Task type, synced as a Linear label |
| `url` | URL | Linked Linear issue URL |

Supported task priorities:

| Notion | Linear |
|--------|--------|
| `Urgent` | `1` |
| `High` | `2` |
| `Medium` | `3` |
| `Low` | `4` |

Task types synced as Linear labels:

- `Redo`
- `Plan`
- `Fix`
- `Test`
- `Design`
- `Research`
- `Write`
- `Build`
- `Organize`
- `Study`
- `Watch`
- `Share`

## CLI Usage

All commands are run through the module entrypoint:

```bash
python -m src.main <command>
```

### Sync everything

```bash
python -m src.main sync
```

### Preview changes without writing

```bash
python -m src.main sync --dry-run
```

### Limit the sync scope

```bash
python -m src.main sync --projects-only
python -m src.main sync --milestones-only
python -m src.main sync --tasks-only
```

### Limit the sync direction

```bash
python -m src.main sync --direction notion-to-linear
python -m src.main sync --direction linear-to-notion
```

### Increase log detail

```bash
python -m src.main -v sync
python -m src.main --json-logs sync
```

### Inspect saved sync state

```bash
python -m src.main status projects
python -m src.main status milestones
python -m src.main status tasks
```

This shows the number of saved sync records and the most recent record links stored in the state database.

### Reset local sync state

```bash
python -m src.main reset
python -m src.main reset --yes
```

Use this when you intentionally want the next run to behave like a first sync.

## How the Sync Works

The Python service follows the same high-level flow for each entity type:

1. Fetch records from Notion and Linear.
2. Normalize both sides into shared internal models.
3. Match records using saved state and the `linear id` property.
4. Detect new records, updates, and conflicts.
5. Resolve conflicts using the configured strategy.
6. Apply creates and updates.
7. Persist post-sync timestamps and content hashes.

### Sync order

The service runs entities in dependency order:

1. Projects
2. Milestones
3. Tasks

Tasks are ordered so parent tasks are created before subtasks.

### Conflict resolution

When both Notion and Linear changed since the last saved sync state:

- `last-write-wins` chooses the most recently modified side.
- `notion-primary` always prefers Notion.
- `linear-primary` always prefers Linear.

### How records are identified

- The `linear sync` checkbox determines whether a Notion record is eligible.
- The `linear id` property stores the Linear-side ID on the Notion record.
- The local state store records the last synced timestamps and a content hash.

## Optional n8n Automation

The `n8n/workflows/` directory contains optional workflow exports for running the sync from a self-hosted n8n instance.

Included workflows:

- `notion-linear-projects-sync.json`
- `notion-linear-milestones-sync.json`
- `notion-linear-tasks-sync.json`
- `notion-linear-error-log.json`
- `notion-linear-shared-reconcile.json`
- `notion-linear-shared-state-upsert.json`
- `notion-linear-shared-task-sort.json`

### Important notes for n8n

- These exports assume a self-hosted n8n instance with the Execute Command node enabled.
- The main workflows are exported inactive so you can review and configure them before enabling them.
- The Notion Trigger nodes are intentionally left mostly unconfigured so you can bind them to your own databases in the UI.
- The workflows rely on environment variables instead of embedded secrets.
- The workflows expect a shared JSON state file, usually inside a mounted n8n data volume.

### Suggested workflow order

1. Projects sync
2. Milestones sync
3. Tasks sync

The task workflow expects project and milestone mappings to already exist in the shared state file.

### Suggested schedules from the exports

- Projects: every 15 minutes
- Milestones: every 15 minutes
- Tasks: every 5 minutes

## Development

### Run tests

```bash
pytest tests/
```

### Install or update dependencies

```bash
pip install -r requirements.txt
```

### Main Python dependencies

- `notion-client`
- `httpx`
- `python-dotenv`
- `click`
- `python-dateutil`

## Security Notes

- Do not commit `.env`.
- Treat `sync_state.db` as local runtime state, not project data.
- If you use the n8n exports, keep secrets in environment variables or the n8n credential store, not inside workflow JSON.
- Review workflow exports before publishing changes if you edited nodes in the UI.

## Troubleshooting

### Missing required environment variables

- Re-check `.env` against `.env.example`.
- Run `python -m src.main validate`.

### Linear team not found

- Confirm `LINEAR_TEAM_KEY` matches your actual team key.
- Or set `LINEAR_TEAM_ID` directly.

### Notion database not accessible

- Make sure the integration is shared with each target database.
- Double-check the database IDs in `.env`.

### Records are not syncing from Notion

- Confirm the record's `linear sync` checkbox is enabled.
- Confirm the property names in Notion match the names documented above.

### Status names do not match exactly

- Task status matching is based on workflow state semantics, not only raw status-name equality.
- Project status names must map to the supported project status values listed above.

### Rate limits

- Notion is handled conservatively at roughly 3 requests per second.
- Linear is handled conservatively for the standard hourly rate limit.
- Use `--dry-run` when testing changes to mappings or schema.

## Project Structure

```text
src/
   config.py
   main.py
   clients/
      linear_client.py
      notion_client.py
   mappers/
      milestone_mapper.py
      project_mapper.py
      task_mapper.py
   models/
      base.py
      milestone.py
      project.py
      task.py
   sync/
      engine.py
      reconciler.py
      relation_resolver.py
   utils/
      logging.py
      retry.py
      state.py
tests/
n8n/workflows/
```

## License

This repository does not currently include a license file.

If you want the project to be MIT-licensed on GitHub, add a `LICENSE` file before publishing and keep the license section consistent with that file.
