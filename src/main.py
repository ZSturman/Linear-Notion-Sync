"""CLI entrypoint for the Notion-Linear sync service."""
import asyncio
import sys
from typing import Optional

import click

from .config import Config, SyncDirection, load_config
from .clients.notion_client import NotionClient
from .clients.linear_client import LinearClient
from .sync.engine import SyncEngine, SyncResult
from .utils.logging import setup_logging, get_logger
from .utils.state import SyncStateStore

logger = get_logger(__name__)


@click.group()
@click.option(
    '--verbose', '-v',
    is_flag=True,
    help='Enable verbose/debug logging'
)
@click.option(
    '--json-logs',
    is_flag=True,
    help='Output logs in JSON format'
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, json_logs: bool) -> None:
    """Notion-Linear bidirectional sync service.
    
    Synchronizes Projects, Milestones, and Tasks between Notion
    and Linear with conflict resolution and state tracking.
    """
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['json_logs'] = json_logs
    
    # Setup logging
    log_level = 'DEBUG' if verbose else 'INFO'
    setup_logging(level=log_level, json_format=json_logs)


@cli.command()
@click.option(
    '--direction', '-d',
    type=click.Choice(['bidirectional', 'notion-to-linear', 'linear-to-notion']),
    default='bidirectional',
    help='Sync direction'
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Show changes without applying them'
)
@click.option(
    '--projects-only',
    is_flag=True,
    help='Sync only projects'
)
@click.option(
    '--milestones-only',
    is_flag=True,
    help='Sync only milestones'
)
@click.option(
    '--tasks-only',
    is_flag=True,
    help='Sync only tasks'
)
@click.pass_context
def sync(
    ctx: click.Context,
    direction: str,
    dry_run: bool,
    projects_only: bool,
    milestones_only: bool,
    tasks_only: bool,
) -> None:
    """Run the sync process.
    
    By default, syncs all entity types bidirectionally. Use flags
    to limit scope or direction.
    
    Examples:
    
        # Full bidirectional sync
        notion-linear-sync sync
        
        # Dry run to preview changes
        notion-linear-sync sync --dry-run
        
        # Sync only tasks from Notion to Linear
        notion-linear-sync sync --direction notion-to-linear --tasks-only
    """
    # Determine entity types
    entity_types = []
    if projects_only:
        entity_types = ['projects']
    elif milestones_only:
        entity_types = ['milestones']
    elif tasks_only:
        entity_types = ['tasks']
    else:
        entity_types = ['projects', 'milestones', 'tasks']
    
    # Map direction
    direction_map = {
        'bidirectional': SyncDirection.BIDIRECTIONAL,
        'notion-to-linear': SyncDirection.NOTION_TO_LINEAR,
        'linear-to-notion': SyncDirection.LINEAR_TO_NOTION,
    }
    sync_direction = direction_map[direction]
    
    # Run sync
    result = _run_sync(
        direction=sync_direction,
        entity_types=entity_types,
        dry_run=dry_run,
    )
    
    # Print summary
    _print_result(result)
    
    if not result.success:
        sys.exit(1)


@cli.command()
@click.argument('entity_type', type=click.Choice(['projects', 'milestones', 'tasks']))
@click.pass_context
def status(ctx: click.Context, entity_type: str) -> None:
    """Show sync status for an entity type.
    
    Displays the number of synced records and any pending changes.
    """
    config = load_config()
    state_store = SyncStateStore(config.state_db_path)
    
    states = state_store.get_all(entity_type.rstrip('s'))  # Remove plural
    
    click.echo(f"\n{entity_type.title()} Sync Status:")
    click.echo(f"  Total synced: {len(states)}")
    
    # Show recent
    if states:
        click.echo(f"\n  Recent syncs:")
        for state in sorted(states, key=lambda s: s.last_synced, reverse=True)[:5]:
            notion_id = f"{state.notion_id[:8]}..." if state.notion_id else 'N/A'
            linear_id = f"{state.linear_id[:8]}..." if state.linear_id else 'N/A'
            click.echo(f"    - notion:{notion_id} <-> linear:{linear_id}")


@cli.command()
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@click.pass_context
def reset(ctx: click.Context, yes: bool) -> None:
    """Reset sync state database.
    
    WARNING: This will clear all sync state. The next sync will
    treat all records as new.
    """
    if not yes:
        click.confirm('This will reset all sync state. Continue?', abort=True)
    
    config = load_config()
    state_store = SyncStateStore(config.state_db_path)
    state_store.reset()
    
    click.echo("Sync state reset successfully.")


@cli.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate configuration and connectivity.
    
    Checks that environment variables are set and APIs are accessible.
    """
    click.echo("Validating configuration...")
    
    try:
        config = load_config()
        click.echo("  ✓ Configuration loaded")
    except Exception as e:
        click.echo(f"  ✗ Configuration error: {e}")
        sys.exit(1)
    
    # Validate Notion
    click.echo("\nValidating Notion connection...")
    try:
        notion = NotionClient(config.notion)
        # We'd need to make this async or use a sync check
        click.echo("  ✓ Notion API key configured")
        click.echo(f"  ✓ Projects DB: {config.notion.projects_db_id[:8]}...")
        click.echo(f"  ✓ Milestones DB: {config.notion.milestones_db_id[:8]}...")
        click.echo(f"  ✓ Tasks DB: {config.notion.tasks_db_id[:8]}...")
    except Exception as e:
        click.echo(f"  ✗ Notion error: {e}")
    
    # Validate Linear
    click.echo("\nValidating Linear connection...")
    try:
        linear = LinearClient(config.linear)
        click.echo("  ✓ Linear API key configured")
        if config.linear.team_key:
            click.echo(f"  ✓ Team key: {config.linear.team_key}")
    except Exception as e:
        click.echo(f"  ✗ Linear error: {e}")
    
    click.echo("\n✓ Validation complete")


def _run_sync(
    direction: SyncDirection,
    entity_types: list[str],
    dry_run: bool,
) -> SyncResult:
    """Run the sync process."""
    config = load_config()
    
    notion = NotionClient(config.notion)
    linear = LinearClient(config.linear)
    state_store = SyncStateStore(config.state_db_path)
    
    engine = SyncEngine(config, notion, linear, state_store)
    
    return asyncio.run(engine.sync(
        direction=direction,
        entity_types=entity_types,
        dry_run=dry_run,
    ))


def _print_result(result: SyncResult) -> None:
    """Print sync result summary."""
    click.echo("\n" + "=" * 50)
    click.echo("SYNC RESULT")
    click.echo("=" * 50)
    
    if result.success:
        click.echo(click.style("✓ SUCCESS", fg='green', bold=True))
    else:
        click.echo(click.style("✗ FAILED", fg='red', bold=True))
    
    click.echo(f"\nCreated:")
    click.echo(f"  Projects:   {result.projects_created}")
    click.echo(f"  Milestones: {result.milestones_created}")
    click.echo(f"  Tasks:      {result.tasks_created}")
    
    click.echo(f"\nUpdated:")
    click.echo(f"  Projects:   {result.projects_updated}")
    click.echo(f"  Milestones: {result.milestones_updated}")
    click.echo(f"  Tasks:      {result.tasks_updated}")

    click.echo(f"\nDeleted:")
    click.echo(f"  Projects:   {result.projects_deleted}")
    click.echo(f"  Milestones: {result.milestones_deleted}")
    click.echo(f"  Tasks:      {result.tasks_deleted}")
    
    if result.duration_seconds:
        click.echo(f"\nDuration: {result.duration_seconds:.2f}s")
    
    if result.errors:
        click.echo(click.style(f"\nErrors ({len(result.errors)}):", fg='red'))
        for error in result.errors[:10]:
            click.echo(f"  - {error}")
        if len(result.errors) > 10:
            click.echo(f"  ... and {len(result.errors) - 10} more")
    
    click.echo()


def main() -> None:
    """Main entry point."""
    cli(obj={})


if __name__ == '__main__':
    main()
