"""Linear GraphQL API client with pagination and retry support."""

from typing import Any, Optional

import httpx

from ..config import LinearConfig
from ..utils.logging import get_logger
from ..utils.retry import with_retry, RateLimiter, RateLimitError

logger = get_logger(__name__)


class LinearClientError(Exception):
    """Error from Linear API."""
    pass


class LinearClient:
    """GraphQL client for Linear API with pagination and retry support.
    
    Provides high-level methods for managing Projects, ProjectMilestones,
    and Issues with automatic pagination and rate limiting.
    """
    
    def __init__(self, config: LinearConfig):
        """Initialize Linear client.
        
        Args:
            config: Linear API configuration
        """
        self.config = config
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={
                "Authorization": config.api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._rate_limiter = RateLimiter(requests_per_second=1.4)  # ~5000/hour
        
        # Cached workflow states
        self._workflow_states: Optional[dict[str, dict]] = None
        self._labels: Optional[dict[str, str]] = None  # name -> id

        # Resolve team id lazily at startup if only team key is provided.
        self._ensure_team_id()
        
        logger.info("Linear client initialized")

    def _ensure_team_id(self) -> None:
        """Ensure config.team_id is set, resolving from team key when needed."""
        if self.config.team_id:
            return

        query = """
        query GetTeams {
            teams(first: 100) {
                nodes {
                    id
                    key
                    name
                }
            }
        }
        """
        data = self._execute(query)
        teams = data.get("teams", {}).get("nodes", [])
        if not teams:
            raise LinearClientError("No Linear teams found for the authenticated user")

        team = None
        if self.config.team_key:
            team = next((t for t in teams if t.get("key") == self.config.team_key), None)
            if team is None:
                raise LinearClientError(
                    f"Linear team key '{self.config.team_key}' not found"
                )
        else:
            team = teams[0]

        self.config.team_id = team["id"]
        if not self.config.team_key:
            self.config.team_key = team["key"]

        logger.info(f"Using Linear team: {team.get('name')} ({team.get('key')})")
    
    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
    
    # -------------------------------------------------------------------------
    # GraphQL execution
    # -------------------------------------------------------------------------
    
    @with_retry(max_retries=3, base_delay=1.0, retryable_exceptions=(httpx.HTTPError,))
    def _execute(self, query: str, variables: Optional[dict] = None) -> dict:
        """Execute a GraphQL query.
        
        Args:
            query: GraphQL query string
            variables: Query variables
            
        Returns:
            Response data
            
        Raises:
            LinearClientError: On API errors
            RateLimitError: On rate limit exceeded
        """
        self._rate_limiter.wait_sync()
        
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        
        try:
            response = self._client.post("", json=payload)
            
            # Check for rate limiting
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                raise RateLimitError(
                    "Linear API rate limit exceeded",
                    retry_after=float(retry_after),
                )

            try:
                result = response.json()
            except Exception:
                result = None

            if response.status_code >= 400:
                logger.error(
                    "Linear API bad response %s: %s",
                    response.status_code,
                    result if result is not None else response.text,
                )
                if result is not None and "errors" in result:
                    errors = result["errors"]
                    error_msg = "; ".join(e.get("message", str(e)) for e in errors)
                    logger.error("Linear GraphQL full errors: %s", errors)
                    raise LinearClientError(f"GraphQL errors: {error_msg}")
                response.raise_for_status()
            
            if result is None:
                raise LinearClientError("Linear API returned non-JSON response")
            
            if "errors" in result:
                errors = result["errors"]
                error_msg = "; ".join(e.get("message", str(e)) for e in errors)
                logger.error("Linear GraphQL full errors: %s", errors)
                logger.error(f"Linear GraphQL errors: {error_msg}")
                raise LinearClientError(f"GraphQL errors: {error_msg}")
            
            return result.get("data", {})
            
        except httpx.HTTPError as e:
            logger.error(f"Linear HTTP error: {e}")
            raise
    
    # -------------------------------------------------------------------------
    # Team and workflow state queries
    # -------------------------------------------------------------------------
    
    def get_team(self) -> dict:
        """Get team information.
        
        Returns:
            Team object
        """
        query = """
        query GetTeam($id: String!) {
            team(id: $id) {
                id
                name
                key
            }
        }
        """
        result = self._execute(query, {"id": self.config.team_id})
        return result.get("team", {})
    
    def get_workflow_states(self, force_refresh: bool = False) -> dict[str, dict]:
        """Get workflow states for the team.
        
        Args:
            force_refresh: Force refresh of cached states
            
        Returns:
            Dict mapping state ID to state object with id, name, type, color
        """
        if self._workflow_states and not force_refresh:
            return self._workflow_states
        
        query = """
        query GetWorkflowStates($teamId: ID!) {
            workflowStates(filter: { team: { id: { eq: $teamId } } }) {
                nodes {
                    id
                    name
                    type
                    color
                    position
                }
            }
        }
        """
        result = self._execute(query, {"teamId": self.config.team_id})
        nodes = result.get("workflowStates", {}).get("nodes", [])
        
        self._workflow_states = {state["id"]: state for state in nodes}
        logger.info(f"Loaded {len(self._workflow_states)} workflow states")
        
        return self._workflow_states
    
    def get_state_by_type(self, state_type: str) -> Optional[dict]:
        """Get first workflow state matching type.
        
        Args:
            state_type: State type (backlog, unstarted, started, completed, canceled)
            
        Returns:
            State object or None
        """
        states = self.get_workflow_states()
        for state in states.values():
            if state.get("type") == state_type:
                return state
        return None
    
    def get_completed_state(self) -> Optional[dict]:
        """Get the 'completed' workflow state."""
        return self.get_state_by_type("completed")
    
    def get_default_state(self) -> Optional[dict]:
        """Get the default (backlog/unstarted) workflow state."""
        return self.get_state_by_type("backlog") or self.get_state_by_type("unstarted")
    
    # -------------------------------------------------------------------------
    # Labels
    # -------------------------------------------------------------------------
    
    def get_labels(self, force_refresh: bool = False) -> dict[str, str]:
        """Get labels for the team.
        
        Args:
            force_refresh: Force refresh of cached labels
            
        Returns:
            Dict mapping label name to label ID
        """
        if self._labels and not force_refresh:
            return self._labels
        
        query = """
        query GetLabels($teamId: ID!) {
            issueLabels(filter: { team: { id: { eq: $teamId } } }) {
                nodes {
                    id
                    name
                    color
                }
            }
        }
        """
        result = self._execute(query, {"teamId": self.config.team_id})
        nodes = result.get("issueLabels", {}).get("nodes", [])
        
        self._labels = {label["name"]: label["id"] for label in nodes}
        logger.info(f"Loaded {len(self._labels)} labels")
        
        return self._labels
    
    def create_label(self, name: str, color: Optional[str] = None) -> dict:
        """Create a new label.
        
        Args:
            name: Label name
            color: Optional hex color (e.g., "#FF0000")
            
        Returns:
            Created label object
        """
        mutation = """
        mutation CreateLabel($input: IssueLabelCreateInput!) {
            issueLabelCreate(input: $input) {
                success
                issueLabel {
                    id
                    name
                    color
                }
            }
        }
        """
        
        input_data = {
            "teamId": self.config.team_id,
            "name": name,
        }
        if color:
            input_data["color"] = color
        
        result = self._execute(mutation, {"input": input_data})
        label_result = result.get("issueLabelCreate", {})
        
        if label_result.get("success"):
            label = label_result.get("issueLabel", {})
            if self._labels is not None:
                self._labels[label["name"]] = label["id"]
            logger.info(f"Created label: {name}")
            return label
        
        raise LinearClientError(f"Failed to create label: {name}")
    
    def ensure_label(self, name: str) -> str:
        """Ensure a label exists, creating if necessary.
        
        Args:
            name: Label name
            
        Returns:
            Label ID
        """
        labels = self.get_labels()
        if name in labels:
            return labels[name]
        
        label = self.create_label(name)
        return label["id"]
    
    # -------------------------------------------------------------------------
    # Projects
    # -------------------------------------------------------------------------
    
    def get_projects(self) -> list[dict]:
        """Get all projects for the team.
        
        Returns:
            List of project objects
        """
        query = """
        query GetProjects($id: String!, $after: String) {
            team(id: $id) {
                projects(first: 100, after: $after) {
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                    nodes {
                        id
                        name
                        description
                        state
                        startDate
                        targetDate
                        url
                        updatedAt
                        createdAt
                    }
                }
            }
        }
        """
        
        all_projects = []
        cursor = None
        
        while True:
            result = self._execute(query, {"id": self.config.team_id, "after": cursor})
            projects_data = result.get("team", {}).get("projects", {})
            
            all_projects.extend(projects_data.get("nodes", []))
            
            page_info = projects_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        
        logger.info(f"Fetched {len(all_projects)} projects")
        return all_projects
    
    def get_project(self, project_id: str) -> dict:
        """Get a single project by ID.
        
        Args:
            project_id: Project ID
            
        Returns:
            Project object
        """
        query = """
        query GetProject($id: String!) {
            project(id: $id) {
                id
                name
                description
                state
                startDate
                targetDate
                url
                updatedAt
            }
        }
        """
        result = self._execute(query, {"id": project_id})
        return result.get("project", {})
    
    def create_project(
        self,
        name: str,
        description: Optional[str] = None,
        state: str = "started",
        start_date: Optional[str] = None,
    ) -> dict:
        """Create a new project.
        
        Args:
            name: Project name
            description: Optional description
            state: Project state (planned, started, paused, completed, canceled)
            start_date: Optional start date (ISO format)
            
        Returns:
            Created project object
        """
        mutation = """
        mutation CreateProject($input: ProjectCreateInput!) {
            projectCreate(input: $input) {
                success
                project {
                    id
                    name
                    description
                    state
                    url
                    updatedAt
                }
            }
        }
        """
        
        input_data = {
            "name": name,
            "teamIds": [self.config.team_id],
            "state": state,
        }
        if description:
            cleaned = description.strip()
            input_data["description"] = cleaned[:255]
        if start_date:
            input_data["startDate"] = start_date
        
        result = self._execute(mutation, {"input": input_data})
        project_result = result.get("projectCreate", {})
        
        if project_result.get("success"):
            project = project_result.get("project", {})
            logger.info(f"Created project: {name} ({project.get('id')})")
            return project
        
        raise LinearClientError(f"Failed to create project: {name}")
    
    def update_project(self, project_id: str, **kwargs) -> dict:
        """Update a project.
        
        Args:
            project_id: Project ID
            **kwargs: Fields to update (name, description, state, startDate, targetDate)
            
        Returns:
            Updated project object
        """
        mutation = """
        mutation UpdateProject($id: String!, $input: ProjectUpdateInput!) {
            projectUpdate(id: $id, input: $input) {
                success
                project {
                    id
                    name
                    description
                    state
                    url
                    updatedAt
                }
            }
        }
        """
        
        result = self._execute(mutation, {"id": project_id, "input": kwargs})
        project_result = result.get("projectUpdate", {})
        
        if project_result.get("success"):
            project = project_result.get("project", {})
            logger.info(f"Updated project: {project_id}")
            return project
        
        raise LinearClientError(f"Failed to update project: {project_id}")
    
    # -------------------------------------------------------------------------
    # Project Milestones
    # -------------------------------------------------------------------------
    
    def get_milestones(self, project_id: Optional[str] = None) -> list[dict]:
        """Get project milestones.
        
        Args:
            project_id: Optional project ID to filter by
            
        Returns:
            List of milestone objects
        """
        if project_id:
            query = """
            query GetMilestones($projectId: ID!, $after: String) {
                projectMilestones(
                    filter: { project: { id: { eq: $projectId } } }
                    first: 100
                    after: $after
                ) {
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                    nodes {
                        id
                        name
                        description
                        targetDate
                        updatedAt
                        project {
                            id
                        }
                    }
                }
            }
            """
            variables = {"projectId": project_id, "after": None}
        else:
            query = """
            query GetAllMilestones($after: String) {
                projectMilestones(first: 100, after: $after) {
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                    nodes {
                        id
                        name
                        description
                        targetDate
                        updatedAt
                        project {
                            id
                        }
                    }
                }
            }
            """
            variables = {"after": None}
        
        all_milestones = []
        cursor = None
        
        while True:
            variables["after"] = cursor
            result = self._execute(query, variables)
            milestones_data = result.get("projectMilestones", {})
            
            all_milestones.extend(milestones_data.get("nodes", []))
            
            page_info = milestones_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        
        logger.info(f"Fetched {len(all_milestones)} milestones")
        return all_milestones
    
    def create_milestone(
        self,
        project_id: str,
        name: str,
        description: Optional[str] = None,
        target_date: Optional[str] = None,
    ) -> dict:
        """Create a project milestone.
        
        Args:
            project_id: Parent project ID
            name: Milestone name
            description: Optional description
            target_date: Optional target date (ISO format)
            
        Returns:
            Created milestone object
        """
        mutation = """
        mutation CreateMilestone($input: ProjectMilestoneCreateInput!) {
            projectMilestoneCreate(input: $input) {
                success
                projectMilestone {
                    id
                    name
                    description
                    targetDate
                    updatedAt
                    project {
                        id
                    }
                }
            }
        }
        """
        
        input_data = {
            "projectId": project_id,
            "name": name,
        }
        if description:
            input_data["description"] = description
        if target_date:
            input_data["targetDate"] = target_date
        
        result = self._execute(mutation, {"input": input_data})
        milestone_result = result.get("projectMilestoneCreate", {})
        
        if milestone_result.get("success"):
            milestone = milestone_result.get("projectMilestone", {})
            logger.info(f"Created milestone: {name} ({milestone.get('id')})")
            return milestone
        
        raise LinearClientError(f"Failed to create milestone: {name}")
    
    def update_milestone(self, milestone_id: str, **kwargs) -> dict:
        """Update a project milestone.
        
        Args:
            milestone_id: Milestone ID
            **kwargs: Fields to update (name, description, targetDate)
            
        Returns:
            Updated milestone object
        """
        mutation = """
        mutation UpdateMilestone($id: String!, $input: ProjectMilestoneUpdateInput!) {
            projectMilestoneUpdate(id: $id, input: $input) {
                success
                projectMilestone {
                    id
                    name
                    description
                    targetDate
                    updatedAt
                }
            }
        }
        """
        
        result = self._execute(mutation, {"id": milestone_id, "input": kwargs})
        milestone_result = result.get("projectMilestoneUpdate", {})
        
        if milestone_result.get("success"):
            return milestone_result.get("projectMilestone", {})
        
        raise LinearClientError(f"Failed to update milestone: {milestone_id}")
    
    # -------------------------------------------------------------------------
    # Issues
    # -------------------------------------------------------------------------
    
    def get_issues(
        self,
        project_id: Optional[str] = None,
        include_completed: bool = True,
    ) -> list[dict]:
        """Get issues for the team.
        
        Args:
            project_id: Optional project ID to filter by
            include_completed: Include completed/canceled issues
            
        Returns:
            List of issue objects
        """
        filters = [f'team: {{ id: {{ eq: "{self.config.team_id}" }} }}']
        
        if project_id:
            filters.append(f'project: {{ id: {{ eq: "{project_id}" }} }}')
        
        filter_str = ", ".join(filters)
        
        query = f"""
        query GetIssues($after: String) {{
            issues(
                filter: {{ {filter_str} }}
                first: 100
                after: $after
            ) {{
                pageInfo {{
                    hasNextPage
                    endCursor
                }}
                nodes {{
                    id
                    identifier
                    title
                    description
                    priority
                    dueDate
                    url
                    updatedAt
                    createdAt
                    state {{
                        id
                        name
                        type
                    }}
                    project {{
                        id
                    }}
                    projectMilestone {{
                        id
                    }}
                    parent {{
                        id
                    }}
                    labels {{
                        nodes {{
                            id
                            name
                        }}
                    }}
                }}
            }}
        }}
        """
        
        all_issues = []
        cursor = None
        
        while True:
            result = self._execute(query, {"after": cursor})
            issues_data = result.get("issues", {})
            
            nodes = issues_data.get("nodes", [])
            
            if not include_completed:
                nodes = [
                    issue for issue in nodes
                    if issue.get("state", {}).get("type") not in ("completed", "canceled")
                ]
            
            all_issues.extend(nodes)
            
            page_info = issues_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        
        logger.info(f"Fetched {len(all_issues)} issues")
        return all_issues
    
    def get_issue(self, issue_id: str) -> dict:
        """Get a single issue by ID.
        
        Args:
            issue_id: Issue ID
            
        Returns:
            Issue object
        """
        query = """
        query GetIssue($id: String!) {
            issue(id: $id) {
                id
                identifier
                title
                description
                priority
                dueDate
                url
                updatedAt
                state {
                    id
                    name
                    type
                }
                project {
                    id
                }
                projectMilestone {
                    id
                }
                parent {
                    id
                }
                labels {
                    nodes {
                        id
                        name
                    }
                }
            }
        }
        """
        result = self._execute(query, {"id": issue_id})
        return result.get("issue", {})
    
    def create_issue(
        self,
        title: str,
        description: Optional[str] = None,
        priority: int = 0,
        due_date: Optional[str] = None,
        project_id: Optional[str] = None,
        milestone_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        state_id: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
    ) -> dict:
        """Create a new issue.
        
        Args:
            title: Issue title
            description: Optional description
            priority: Priority (0-4)
            due_date: Optional due date (ISO format)
            project_id: Optional project ID
            milestone_id: Optional milestone ID
            parent_id: Optional parent issue ID
            state_id: Optional workflow state ID
            label_ids: Optional list of label IDs
            
        Returns:
            Created issue object
        """
        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                    updatedAt
                    state {
                        id
                        name
                        type
                    }
                }
            }
        }
        """
        
        input_data = {
            "teamId": self.config.team_id,
            "title": title,
            "priority": priority,
        }
        
        if description:
            input_data["description"] = description
        if due_date:
            input_data["dueDate"] = due_date
        if project_id:
            input_data["projectId"] = project_id
        if milestone_id:
            input_data["projectMilestoneId"] = milestone_id
        if parent_id:
            input_data["parentId"] = parent_id
        if state_id:
            input_data["stateId"] = state_id
        if label_ids:
            input_data["labelIds"] = label_ids
        
        result = self._execute(mutation, {"input": input_data})
        issue_result = result.get("issueCreate", {})
        
        if issue_result.get("success"):
            issue = issue_result.get("issue", {})
            logger.info(f"Created issue: {title} ({issue.get('identifier')})")
            return issue
        
        raise LinearClientError(f"Failed to create issue: {title}")
    
    def update_issue(self, issue_id: str, **kwargs) -> dict:
        """Update an issue.
        
        Args:
            issue_id: Issue ID
            **kwargs: Fields to update (title, description, priority, dueDate,
                     projectId, projectMilestoneId, parentId, stateId, labelIds)
            
        Returns:
            Updated issue object
        """
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                    updatedAt
                    state {
                        id
                        name
                        type
                    }
                }
            }
        }
        """
        
        result = self._execute(mutation, {"id": issue_id, "input": kwargs})
        issue_result = result.get("issueUpdate", {})
        
        if issue_result.get("success"):
            issue = issue_result.get("issue", {})
            logger.info(f"Updated issue: {issue_id}")
            return issue
        
        raise LinearClientError(f"Failed to update issue: {issue_id}")

    def delete_issue(self, issue_id: str) -> None:
        """Delete an issue."""
        mutation = """
        mutation DeleteIssue($id: String!) {
            issueDelete(id: $id) {
                success
            }
        }
        """

        result = self._execute(mutation, {"id": issue_id})
        if result.get("issueDelete", {}).get("success"):
            logger.info(f"Deleted issue: {issue_id}")
            return

        raise LinearClientError(f"Failed to delete issue: {issue_id}")

    def delete_project(self, project_id: str) -> None:
        """Delete a project."""
        mutation = """
        mutation DeleteProject($id: String!) {
            projectDelete(id: $id) {
                success
            }
        }
        """

        result = self._execute(mutation, {"id": project_id})
        if result.get("projectDelete", {}).get("success"):
            logger.info(f"Deleted project: {project_id}")
            return

        raise LinearClientError(f"Failed to delete project: {project_id}")

    def delete_milestone(self, milestone_id: str) -> None:
        """Delete a project milestone."""
        mutation = """
        mutation DeleteMilestone($id: String!) {
            projectMilestoneDelete(id: $id) {
                success
            }
        }
        """

        result = self._execute(mutation, {"id": milestone_id})
        if result.get("projectMilestoneDelete", {}).get("success"):
            logger.info(f"Deleted milestone: {milestone_id}")
            return

        raise LinearClientError(f"Failed to delete milestone: {milestone_id}")