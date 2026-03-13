# Notion-Linear Sync Service

A Python-based bidirectional synchronization service between Notion and Linear for Projects, Milestones, and Tasks.

## Features

- **Bidirectional Sync**: Changes in either system are synced to the other
- **Entity Support**: Projects, Milestones (Project Milestones), and Tasks (Issues)
- **Relation Preservation**: Maintains relationships between entities (project↔milestone, task↔project, parent↔subtask)
- **Conflict Resolution**: Configurable strategies (last-write-wins, notion-primary, linear-primary)
- **State Tracking**: SQLite-based persistence to track sync state and prevent duplicates
- **Selective Sync**: Uses `linear sync` checkbox in Notion to control which records sync
- **Dry Run Mode**: Preview changes before applying them

## Requirements

- Python 3.10+
- Notion Integration with access to Projects, Milestones, and Tasks databases
- Linear API key

## Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd notion-linear-sync
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy the example environment file and configure:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and database IDs
   ```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NOTION_TOKEN` | Yes | Notion integration token |
| `NOTION_PROJECTS_DB_ID` | Yes | Notion Projects database ID |
| `NOTION_MILESTONES_DB_ID` | Yes | Notion Milestones database ID |
| `NOTION_TASKS_DB_ID` | Yes | Notion Tasks database ID |
| `LINEAR_API_KEY` | Yes | Linear API key |
| `LINEAR_TEAM_KEY` | No | Linear team key (e.g., "PROJ") |
| `SYNC_STATE_PATH` | No | Path to sync state database (default: `./sync_state.db`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `CONFLICT_STRATEGY` | No | Conflict resolution strategy (default: `last-write-wins`) |

### Notion Database Setup

Each Notion database must have these properties:

#### Projects Database
| Property | Type | Purpose |
|----------|------|---------|
| `title` | Title | Project name |
| `linear id` | Rich Text | Linear project ID (auto-filled) |
| `linear sync` | Checkbox | Enable sync for this project |
| `status` | Status | Project status (Predevelopment/Active/Dormant/Complete/Abandoned) |
| `one liner` | Rich Text | Project description |
| `started at` | Date | Project start date |

#### Milestones Database
| Property | Type | Purpose |
|----------|------|---------|
| `milestone` | Title | Milestone name |
| `linear id` | Rich Text | Linear milestone ID (auto-filled) |
| `linear sync` | Checkbox | Enable sync for this milestone |
| `description` | Rich Text | Milestone description |
| `project` | Relation | Link to project |
| `due date (manual)` | Date | Due date (write target) |
| `effective due date` | Formula | Due date (read source) |

#### Tasks Database
| Property | Type | Purpose |
|----------|------|---------|
| `task` | Title | Task title |
| `linear id` | Rich Text | Linear issue ID (auto-filled) |
| `linear sync` | Checkbox | Enable sync for this task |
| `complete` | Checkbox | Task completion status |
| `status` | Select | Task workflow status (for example Backlog/In Progress/Done) |
| `project` | Relation | Link to project |
| `milestones` | Relation | Link to milestone |
| `parent task` | Relation | Link to parent task (for subtasks) |
| `priority` | Select | Priority (Low/Medium/High) |
| `due date` | Date | Task due date |
| `type` | Select | Task type (synced as Linear labels) |

## Usage

### Run Full Sync

```bash
# Full bidirectional sync
python -m src.main sync

# Dry run to preview changes
python -m src.main sync --dry-run

# Verbose logging
python -m src.main -v sync
```

### Selective Sync

```bash
# Sync only projects
python -m src.main sync --projects-only

# Sync only tasks from Notion to Linear
python -m src.main sync --direction notion-to-linear --tasks-only
```

### Check Status

```bash
# View sync status for entity type
python -m src.main status projects
python -m src.main status milestones
python -m src.main status tasks
```

### Validate Configuration

```bash
python -m src.main validate
```

### Reset Sync State

```bash
# Reset (with confirmation)
python -m src.main reset

# Force reset without confirmation
python -m src.main reset --yes
```

## Field Mappings

### Priority Mapping

| Notion | Linear |
|--------|--------|
| Low | 4 (Low) |
| Medium | 3 (Medium) |
| High | 2 (High) |
| Urgent | 1 (Urgent) |

### Project Status Mapping

| Notion | Linear |
|--------|--------|
| Predevelopment | planned |
| Active | started |
| Dormant | paused |
| Complete | completed |
| Abandoned | canceled |

### Task Types

The following task types are synced as Linear labels:
- Redo, Plan, Fix, Test, Design, Research, Write, Build, Organize, Study, Watch, Share

## How It Works

### Sync Process

1. **Fetch**: Retrieve all records from both Notion and Linear
2. **Compare**: Detect new, modified, and conflicting records
3. **Resolve**: Apply conflict resolution strategy
4. **Apply**: Create/update records in target systems
5. **Persist**: Update sync state database

### Sync Order

Entities are synced in dependency order:
1. **Projects** (no dependencies)
2. **Milestones** (depend on projects)
3. **Tasks** (depend on projects, milestones, and parent tasks)

Tasks with parent-child relationships are topologically sorted to ensure parents are created before children.

### Identifying Synced Records

- Records with `linear sync` checkbox checked are considered for sync
- The `linear id` property links Notion records to Linear records
- Sync state is persisted in SQLite to track last-modified timestamps

### Conflict Resolution

When both systems have changes since last sync:
- **last-write-wins**: Most recently modified record wins
- **notion-primary**: Notion record always wins
- **linear-primary**: Linear record always wins

## Architecture

```
src/
├── config.py          # Configuration management
├── main.py            # CLI entrypoint
├── clients/
│   ├── notion_client.py   # Notion API wrapper
│   └── linear_client.py   # Linear GraphQL client
├── models/
│   ├── base.py            # Base sync record classes
│   ├── project.py         # UnifiedProject model
│   ├── milestone.py       # UnifiedMilestone model
│   └── task.py            # UnifiedTask model
├── mappers/
│   ├── project_mapper.py  # Project field mapping
│   ├── milestone_mapper.py # Milestone field mapping
│   └── task_mapper.py     # Task field mapping
├── sync/
│   ├── reconciler.py      # Change detection
│   ├── relation_resolver.py # Relation resolution
│   └── engine.py          # Sync orchestration
└── utils/
    ├── logging.py         # Structured logging
    ├── retry.py           # Retry and rate limiting
    └── state.py           # Sync state persistence
```

## Development

### Running Tests

```bash
pytest tests/
```

### Code Style

```bash
# Format code
black src/ tests/

# Type checking
mypy src/
```

## Troubleshooting

### Common Issues

**"Missing required environment variables"**
- Ensure all required variables are set in `.env`
- Run `python -m src.main validate` to check configuration

**"Linear team not found"**
- Check that `LINEAR_TEAM_KEY` matches your team's key
- Leave it empty to use the first available team

**"Notion database not accessible"**
- Ensure your Notion integration has access to all three databases
- The integration must be explicitly shared with each database

**Rate Limiting**
- Notion: 3 requests/second (automatically handled)
- Linear: 5000 requests/hour (automatically handled)
- Use dry run mode for testing to avoid hitting limits

## License

MIT
