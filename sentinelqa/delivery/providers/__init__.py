"""Server-configured delivery providers.

Provider modules expose connector implementations only. Registration and policy
remain owned by the deterministic delivery orchestrator.
"""

from .github import GitHubIssuesConnector, GitHubIssuesDestination
from .http import AsyncHttpTransport, HttpxAsyncTransport, ProviderHttpResponse
from .jira import JiraConnector, JiraDestination
from .slack import SlackConnector, SlackDestination, SlackMessage, build_slack_message
from .ticketing import TicketPayload, build_ticket_payload

__all__ = [
    "AsyncHttpTransport",
    "GitHubIssuesConnector",
    "GitHubIssuesDestination",
    "HttpxAsyncTransport",
    "JiraConnector",
    "JiraDestination",
    "ProviderHttpResponse",
    "SlackConnector",
    "SlackDestination",
    "SlackMessage",
    "TicketPayload",
    "build_slack_message",
    "build_ticket_payload",
]
