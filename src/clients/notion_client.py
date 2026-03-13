"""Notion API client wrapper with pagination and retry support."""

from typing import Any, Optional

from notion_client import Client
from notion_client.errors import APIResponseError

from ..config import NotionConfig
from ..utils.logging import get_logger
from ..utils.retry import with_retry, RateLimiter

logger = get_logger(__name__)


class NotionClientError(Exception):
    """Error from Notion API."""
    pass


class NotionClient:
    """Wrapper around Notion SDK with pagination, filtering, and retry support.
    
    Provides high-level methods for querying databases and managing pages
    with automatic pagination and rate limiting.
    """
    
    def __init__(self, config: NotionConfig):
        """Initialize Notion client.
        
        Args:
            config: Notion API configuration
        """
        self.config = config
        self._client = Client(auth=config.token)
        self._rate_limiter = RateLimiter(requests_per_second=3.0)
        
        logger.info("Notion client initialized")
    
    # -------------------------------------------------------------------------
    # Database queries
    # -------------------------------------------------------------------------
    
    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(APIResponseError,))
    def query_database(
        self,
        database_id: str,
        filter: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Query a Notion database with automatic pagination.
        
        Args:
            database_id: Database ID to query
            filter: Optional filter object
            sorts: Optional sort configuration
            page_size: Number of results per page (max 100)
            
        Returns:
            List of all matching page objects
        """
        all_results = []
        has_more = True
        start_cursor = None

        self._rate_limiter.wait_sync()

        try:
            db_meta = self._client.databases.retrieve(database_id=database_id)
            data_sources = db_meta.get("data_sources", [])
            if not data_sources:
                raise NotionClientError(
                    f"No data sources found for database: {database_id}"
                )
            data_source_id = data_sources[0]["id"]
        except APIResponseError as e:
            logger.error(f"Notion API error retrieving database metadata: {e}")
            raise
        
        while has_more:
            self._rate_limiter.wait_sync()

            query_params = {
                "page_size": page_size,
            }

            if filter:
                query_params["filter"] = filter
            if sorts:
                query_params["sorts"] = sorts
            if start_cursor:
                query_params["start_cursor"] = start_cursor

            try:
                response = self._client.data_sources.query(
                    data_source_id,
                    **query_params,
                )
            except APIResponseError as e:
                logger.error(f"Notion API error querying database: {e}")
                raise
            
            all_results.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")
            
            logger.debug(
                f"Fetched {len(response.get('results', []))} pages, "
                f"total: {len(all_results)}, has_more: {has_more}"
            )
        
        return all_results
    
    def query_synced_records(self, database_id: str) -> list[dict]:
        """Query database for records with linear sync enabled.
        
        Args:
            database_id: Database ID to query
            
        Returns:
            List of pages where 'linear sync' checkbox is checked
        """
        # Determine the linear sync property name based on database
        if database_id == self.config.projects_db_id:
            sync_prop = self.config.project_linear_sync_prop
        elif database_id == self.config.milestones_db_id:
            sync_prop = self.config.milestone_linear_sync_prop
        elif database_id == self.config.tasks_db_id:
            sync_prop = self.config.task_linear_sync_prop
        else:
            sync_prop = "linear sync"
        
        filter_obj = {
            "property": sync_prop,
            "checkbox": {"equals": True}
        }
        
        logger.info(f"Querying synced records from database {database_id[:8]}...")
        results = self.query_database(database_id, filter=filter_obj)
        logger.info(f"Found {len(results)} synced records")
        
        return results
    
    def query_projects(self, synced_only: bool = True) -> list[dict]:
        """Query projects database.
        
        Args:
            synced_only: Only return records with linear sync enabled
            
        Returns:
            List of project pages
        """
        if synced_only:
            return self.query_synced_records(self.config.projects_db_id)
        return self.query_database(self.config.projects_db_id)
    
    def query_milestones(self, synced_only: bool = True) -> list[dict]:
        """Query milestones database.
        
        Args:
            synced_only: Only return records with linear sync enabled
            
        Returns:
            List of milestone pages
        """
        if synced_only:
            return self.query_synced_records(self.config.milestones_db_id)
        return self.query_database(self.config.milestones_db_id)
    
    def query_tasks(self, synced_only: bool = True) -> list[dict]:
        """Query tasks database.
        
        Args:
            synced_only: Only return records with linear sync enabled
            
        Returns:
            List of task pages
        """
        if synced_only:
            return self.query_synced_records(self.config.tasks_db_id)
        return self.query_database(self.config.tasks_db_id)
    
    # -------------------------------------------------------------------------
    # Page operations
    # -------------------------------------------------------------------------
    
    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(APIResponseError,))
    def get_page(self, page_id: str) -> dict:
        """Get a single page by ID.
        
        Args:
            page_id: Page ID to retrieve
            
        Returns:
            Page object
        """
        self._rate_limiter.wait_sync()
        
        try:
            return self._client.pages.retrieve(page_id=page_id)
        except APIResponseError as e:
            logger.error(f"Error retrieving page {page_id}: {e}")
            raise NotionClientError(f"Failed to retrieve page: {e}")
    
    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(APIResponseError,))
    def create_page(
        self,
        database_id: str,
        properties: dict,
        set_linear_sync: bool = True,
    ) -> dict:
        """Create a new page in a database.
        
        Args:
            database_id: Database to create page in
            properties: Page properties
            set_linear_sync: Set linear sync checkbox to true
            
        Returns:
            Created page object
        """
        self._rate_limiter.wait_sync()
        
        # Add linear sync checkbox if requested
        if set_linear_sync:
            if database_id == self.config.projects_db_id:
                sync_prop = self.config.project_linear_sync_prop
            elif database_id == self.config.milestones_db_id:
                sync_prop = self.config.milestone_linear_sync_prop
            elif database_id == self.config.tasks_db_id:
                sync_prop = self.config.task_linear_sync_prop
            else:
                sync_prop = "linear sync"
            
            properties[sync_prop] = {"checkbox": True}
        
        try:
            page = self._client.pages.create(
                parent={"database_id": database_id},
                properties=properties,
            )
            logger.info(f"Created Notion page: {page['id']}")
            return page
        except APIResponseError as e:
            logger.error(f"Error creating page: {e}")
            raise NotionClientError(f"Failed to create page: {e}")
    
    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(APIResponseError,))
    def update_page(self, page_id: str, properties: dict) -> dict:
        """Update an existing page.
        
        Args:
            page_id: Page ID to update
            properties: Properties to update
            
        Returns:
            Updated page object
        """
        self._rate_limiter.wait_sync()
        
        try:
            page = self._client.pages.update(
                page_id=page_id,
                properties=properties,
            )
            logger.info(f"Updated Notion page: {page_id}")
            return page
        except APIResponseError as e:
            logger.error(f"Error updating page {page_id}: {e}")
            raise NotionClientError(f"Failed to update page: {e}")

    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(APIResponseError,))
    def trash_page(self, page_id: str) -> dict:
        """Move a page to the Notion trash."""
        self._rate_limiter.wait_sync()

        try:
            page = self._client.pages.update(page_id=page_id, in_trash=True)
            logger.info(f"Trashed Notion page: {page_id}")
            return page
        except APIResponseError as e:
            logger.error(f"Error trashing page {page_id}: {e}")
            raise NotionClientError(f"Failed to trash page: {e}")
    
    # -------------------------------------------------------------------------
    # Property helpers
    # -------------------------------------------------------------------------
    
    def set_relation(
        self,
        page_id: str,
        property_name: str,
        related_page_ids: list[str],
    ) -> dict:
        """Set a relation property on a page.
        
        Args:
            page_id: Page to update
            property_name: Name of relation property
            related_page_ids: List of page IDs to relate
            
        Returns:
            Updated page object
        """
        properties = {
            property_name: {
                "relation": [{"id": pid} for pid in related_page_ids]
            }
        }
        return self.update_page(page_id, properties)
    
    def set_linear_id(
        self,
        page_id: str,
        linear_id: str,
        entity_type: str = "task",
    ) -> dict:
        """Set the linear id property on a page.
        
        Args:
            page_id: Page to update
            linear_id: Linear ID to set
            entity_type: Entity type (project, milestone, task)
            
        Returns:
            Updated page object
        """
        if entity_type == "project":
            prop_name = self.config.project_linear_id_prop
        elif entity_type == "milestone":
            prop_name = self.config.milestone_linear_id_prop
        else:
            prop_name = self.config.task_linear_id_prop
        
        properties = {
            prop_name: {
                "rich_text": [{"text": {"content": linear_id}}]
            }
        }
        return self.update_page(page_id, properties)
    
    def find_by_linear_id(
        self,
        database_id: str,
        linear_id: str,
        linear_id_prop: str,
    ) -> Optional[dict]:
        """Find a page by its linear id property.
        
        Args:
            database_id: Database to search
            linear_id: Linear ID to find
            linear_id_prop: Name of the linear id property
            
        Returns:
            Page object if found, None otherwise
        """
        filter_obj = {
            "property": linear_id_prop,
            "rich_text": {"equals": linear_id}
        }
        
        results = self.query_database(database_id, filter=filter_obj, page_size=1)
        
        if results:
            return results[0]
        return None
    
    def find_project_by_linear_id(self, linear_id: str) -> Optional[dict]:
        """Find a project page by Linear ID."""
        return self.find_by_linear_id(
            self.config.projects_db_id,
            linear_id,
            self.config.project_linear_id_prop,
        )
    
    def find_milestone_by_linear_id(self, linear_id: str) -> Optional[dict]:
        """Find a milestone page by Linear ID."""
        return self.find_by_linear_id(
            self.config.milestones_db_id,
            linear_id,
            self.config.milestone_linear_id_prop,
        )
    
    def find_task_by_linear_id(self, linear_id: str) -> Optional[dict]:
        """Find a task page by Linear ID."""
        return self.find_by_linear_id(
            self.config.tasks_db_id,
            linear_id,
            self.config.task_linear_id_prop,
        )