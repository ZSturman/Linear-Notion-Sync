"""Sync state persistence using SQLite.

Stores sync state for checkpoint/resume, deduplication, and change detection.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class SyncState:
    """State record for a synced entity."""
    entity_type: str  # "project", "milestone", "task"
    notion_id: Optional[str]
    linear_id: Optional[str]
    notion_last_modified: Optional[datetime]
    linear_last_modified: Optional[datetime]
    content_hash: str
    last_synced: datetime
    
    @staticmethod
    def compute_hash(data: dict) -> str:
        """Compute content hash from entity data.
        
        Args:
            data: Entity data dictionary
            
        Returns:
            SHA256 hash of sorted JSON representation
        """
        # Sort keys for consistent hashing
        normalized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class SyncStateStore:
    """SQLite-backed store for sync state.
    
    Provides:
    - Checkpoint storage for incremental sync
    - Deduplication via notion_id ↔ linear_id mapping
    - Content hashing for conflict detection
    """
    
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sync_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,
        notion_id TEXT,
        linear_id TEXT,
        notion_last_modified TEXT,
        linear_last_modified TEXT,
        content_hash TEXT NOT NULL,
        last_synced TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entity_type, notion_id),
        UNIQUE(entity_type, linear_id)
    );
    
    CREATE INDEX IF NOT EXISTS idx_entity_type ON sync_state(entity_type);
    CREATE INDEX IF NOT EXISTS idx_notion_id ON sync_state(notion_id);
    CREATE INDEX IF NOT EXISTS idx_linear_id ON sync_state(linear_id);
    
    CREATE TABLE IF NOT EXISTS sync_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
    
    def __init__(self, db_path: Path):
        """Initialize state store.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()
        logger.debug(f"Initialized sync state database at {self.db_path}")
    
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection context manager."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_by_notion_id(
        self, entity_type: str, notion_id: str
    ) -> Optional[SyncState]:
        """Get sync state by Notion page ID.
        
        Args:
            entity_type: Type of entity (project, milestone, task)
            notion_id: Notion page ID
            
        Returns:
            SyncState if found, None otherwise
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM sync_state 
                WHERE entity_type = ? AND notion_id = ?
                """,
                (entity_type, notion_id),
            ).fetchone()
            
            if row:
                return self._row_to_state(row)
            return None
    
    def get_by_linear_id(
        self, entity_type: str, linear_id: str
    ) -> Optional[SyncState]:
        """Get sync state by Linear ID.
        
        Args:
            entity_type: Type of entity (project, milestone, task)
            linear_id: Linear entity ID
            
        Returns:
            SyncState if found, None otherwise
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM sync_state 
                WHERE entity_type = ? AND linear_id = ?
                """,
                (entity_type, linear_id),
            ).fetchone()
            
            if row:
                return self._row_to_state(row)
            return None
    
    def get_all(self, entity_type: Optional[str] = None) -> list[SyncState]:
        """Get all sync states, optionally filtered by entity type.
        
        Args:
            entity_type: Optional entity type filter
            
        Returns:
            List of SyncState records
        """
        with self._get_connection() as conn:
            if entity_type:
                rows = conn.execute(
                    "SELECT * FROM sync_state WHERE entity_type = ?",
                    (entity_type,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sync_state").fetchall()
            
            return [self._row_to_state(row) for row in rows]
    
    def upsert(self, state: SyncState) -> None:
        """Insert or update sync state.
        
        Resolves existing records by either unique key:
        - (entity_type, linear_id)
        - (entity_type, notion_id)
        
        Args:
            state: SyncState to save
        """
        notion_last_modified = (
            state.notion_last_modified.isoformat()
            if state.notion_last_modified else None
        )
        linear_last_modified = (
            state.linear_last_modified.isoformat()
            if state.linear_last_modified else None
        )
        last_synced = state.last_synced.isoformat()

        with self._get_connection() as conn:
            existing = None

            if state.linear_id:
                existing = conn.execute(
                    """
                    SELECT id FROM sync_state
                    WHERE entity_type = ? AND linear_id = ?
                    """,
                    (state.entity_type, state.linear_id),
                ).fetchone()

            if not existing and state.notion_id:
                existing = conn.execute(
                    """
                    SELECT id FROM sync_state
                    WHERE entity_type = ? AND notion_id = ?
                    """,
                    (state.entity_type, state.notion_id),
                ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE sync_state
                    SET notion_id = ?,
                        linear_id = ?,
                        notion_last_modified = ?,
                        linear_last_modified = ?,
                        content_hash = ?,
                        last_synced = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        state.notion_id,
                        state.linear_id,
                        notion_last_modified,
                        linear_last_modified,
                        state.content_hash,
                        last_synced,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sync_state (
                        entity_type, notion_id, linear_id,
                        notion_last_modified, linear_last_modified,
                        content_hash, last_synced, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        state.entity_type,
                        state.notion_id,
                        state.linear_id,
                        notion_last_modified,
                        linear_last_modified,
                        state.content_hash,
                        last_synced,
                    ),
                )

            conn.commit()
    
    def delete(self, entity_type: str, notion_id: Optional[str] = None, linear_id: Optional[str] = None) -> bool:
        """Delete sync state record.
        
        Args:
            entity_type: Entity type
            notion_id: Notion page ID (optional)
            linear_id: Linear ID (optional)
            
        Returns:
            True if record was deleted
        """
        with self._get_connection() as conn:
            if notion_id:
                result = conn.execute(
                    "DELETE FROM sync_state WHERE entity_type = ? AND notion_id = ?",
                    (entity_type, notion_id),
                )
            elif linear_id:
                result = conn.execute(
                    "DELETE FROM sync_state WHERE entity_type = ? AND linear_id = ?",
                    (entity_type, linear_id),
                )
            else:
                return False
            
            conn.commit()
            return result.rowcount > 0
    
    def get_linear_id_for_notion(
        self, entity_type: str, notion_id: str
    ) -> Optional[str]:
        """Get Linear ID for a Notion page.
        
        Args:
            entity_type: Entity type
            notion_id: Notion page ID
            
        Returns:
            Linear ID if mapping exists
        """
        state = self.get_by_notion_id(entity_type, notion_id)
        return state.linear_id if state else None
    
    def get_notion_id_for_linear(
        self, entity_type: str, linear_id: str
    ) -> Optional[str]:
        """Get Notion page ID for a Linear entity.
        
        Args:
            entity_type: Entity type
            linear_id: Linear entity ID
            
        Returns:
            Notion page ID if mapping exists
        """
        state = self.get_by_linear_id(entity_type, linear_id)
        return state.notion_id if state else None
    
    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value.
        
        Args:
            key: Metadata key
            value: Value (will be JSON serialized)
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value, default=str)),
            )
            conn.commit()
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value.
        
        Args:
            key: Metadata key
            default: Default value if not found
            
        Returns:
            Stored value or default
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM sync_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            
            if row:
                return json.loads(row["value"])
            return default
    
    def get_last_sync_time(self) -> Optional[datetime]:
        """Get timestamp of last successful sync.
        
        Returns:
            Last sync datetime or None
        """
        value = self.get_metadata("last_sync_time")
        if value:
            return datetime.fromisoformat(value)
        return None
    
    def set_last_sync_time(self, dt: Optional[datetime] = None) -> None:
        """Set last sync time to now or specified time.
        
        Args:
            dt: Datetime to set (defaults to now)
        """
        self.set_metadata("last_sync_time", (dt or datetime.utcnow()).isoformat())
    
    def _row_to_state(self, row: sqlite3.Row) -> SyncState:
        """Convert database row to SyncState object."""
        return SyncState(
            entity_type=row["entity_type"],
            notion_id=row["notion_id"],
            linear_id=row["linear_id"],
            notion_last_modified=(
                datetime.fromisoformat(row["notion_last_modified"])
                if row["notion_last_modified"]
                else None
            ),
            linear_last_modified=(
                datetime.fromisoformat(row["linear_last_modified"])
                if row["linear_last_modified"]
                else None
            ),
            content_hash=row["content_hash"],
            last_synced=datetime.fromisoformat(row["last_synced"]),
        )
