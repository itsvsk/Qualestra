from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .connectors import DeliveryRegistry
from .providers import (
    AsyncHttpTransport,
    GitHubIssuesConnector,
    GitHubIssuesDestination,
    HttpxAsyncTransport,
    JiraConnector,
    JiraDestination,
    SlackConnector,
    SlackDestination,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"
_ENV_REFERENCE_PATTERN = r"^[A-Z_][A-Z0-9_]{0,126}$"
_LABEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$"


class ProviderConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DestinationConfig(ProviderConfigModel):
    destination_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def display_name_is_non_secret_metadata(cls, value: str) -> str:
        normalized = value.strip()
        if "://" in normalized or normalized.lower().startswith(("env:", "secret:", "credential:")):
            raise ValueError("destination display name must contain only non-secret metadata")
        return normalized


class GitHubDestinationConfig(DestinationConfig):
    provider: Literal["github"]
    repository: str
    token_env: str = Field(pattern=_ENV_REFERENCE_PATTERN)
    api_base_url: str = "https://api.github.com"
    web_base_url: str = "https://github.com"
    labels: tuple[str, ...] = ()

    @model_validator(mode="after")
    def provider_values_are_valid(self) -> GitHubDestinationConfig:
        self.to_destination()
        _validate_labels(self.labels)
        return self

    def to_destination(self) -> GitHubIssuesDestination:
        return GitHubIssuesDestination(
            destination_id=self.destination_id,
            repository=self.repository,
            token_env=self.token_env,
            api_base_url=self.api_base_url,
            web_base_url=self.web_base_url,
            labels=self.labels,
        )


class JiraDestinationConfig(DestinationConfig):
    provider: Literal["jira"]
    base_url: str
    project_key: str
    api_token_env: str = Field(pattern=_ENV_REFERENCE_PATTERN)
    user_email_env: str | None = Field(default=None, pattern=_ENV_REFERENCE_PATTERN)
    issue_type: str = "Bug"
    labels: tuple[str, ...] = ()

    @model_validator(mode="after")
    def provider_values_are_valid(self) -> JiraDestinationConfig:
        self.to_destination()
        _validate_labels(self.labels)
        return self

    def to_destination(self) -> JiraDestination:
        return JiraDestination(
            destination_id=self.destination_id,
            base_url=self.base_url,
            project_key=self.project_key,
            api_token_env=self.api_token_env,
            user_email_env=self.user_email_env,
            issue_type=self.issue_type,
            labels=self.labels,
        )


class SlackDestinationConfig(DestinationConfig):
    provider: Literal["slack"]
    channel: str
    token_env: str = Field(pattern=_ENV_REFERENCE_PATTERN)
    endpoint: str = "https://slack.com/api/chat.postMessage"

    @model_validator(mode="after")
    def provider_values_are_valid(self) -> SlackDestinationConfig:
        self.to_destination()
        return self

    def to_destination(self) -> SlackDestination:
        return SlackDestination(
            destination_id=self.destination_id,
            channel=self.channel,
            token_env=self.token_env,
            endpoint=self.endpoint,
        )


ConfiguredDestination = Annotated[
    GitHubDestinationConfig | JiraDestinationConfig | SlackDestinationConfig,
    Field(discriminator="provider"),
]


class DeliveryProviderConfiguration(ProviderConfigModel):
    schema_version: Literal["1.0"] = "1.0"
    destinations: tuple[ConfiguredDestination, ...] = ()

    @model_validator(mode="after")
    def destination_keys_are_unique(self) -> DeliveryProviderConfiguration:
        keys = [(item.provider, item.destination_id) for item in self.destinations]
        if len(keys) != len(set(keys)):
            raise ValueError("provider destination identifiers must be unique per provider")
        return self


def load_delivery_provider_configuration(
    path: str | Path,
) -> DeliveryProviderConfiguration:
    with Path(path).open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, dict):
        raise ValueError("delivery provider configuration must be a YAML mapping")
    return DeliveryProviderConfiguration.model_validate(value)


def register_provider_destinations(
    registry: DeliveryRegistry,
    configuration: DeliveryProviderConfiguration,
    *,
    environment: Mapping[str, str] | None = None,
    ticket_transport: AsyncHttpTransport | None = None,
    slack_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    runtime_environment = os.environ if environment is None else environment
    github_configs = [
        item for item in configuration.destinations if isinstance(item, GitHubDestinationConfig)
    ]
    jira_configs = [
        item for item in configuration.destinations if isinstance(item, JiraDestinationConfig)
    ]
    slack_configs = [
        item for item in configuration.destinations if isinstance(item, SlackDestinationConfig)
    ]

    resolved_ticket_transport = ticket_transport
    if (github_configs or jira_configs) and resolved_ticket_transport is None:
        resolved_ticket_transport = HttpxAsyncTransport(
            httpx.AsyncClient(timeout=10, follow_redirects=False)
        )
        registry.manage(resolved_ticket_transport)

    if github_configs:
        assert resolved_ticket_transport is not None
        github_destinations = {
            item.destination_id: item.to_destination() for item in github_configs
        }
        github_connector = GitHubIssuesConnector(
            github_destinations,
            resolved_ticket_transport,
            environ=runtime_environment,
        )
        for github_config in github_configs:
            registry.register(
                github_connector,
                github_config.destination_id,
                display_name=github_config.display_name,
                kind="ticket",
                provider="github",
            )

    if jira_configs:
        assert resolved_ticket_transport is not None
        jira_destinations = {item.destination_id: item.to_destination() for item in jira_configs}
        jira_connector = JiraConnector(
            jira_destinations,
            resolved_ticket_transport,
            environ=runtime_environment,
        )
        for jira_config in jira_configs:
            registry.register(
                jira_connector,
                jira_config.destination_id,
                display_name=jira_config.display_name,
                kind="ticket",
                provider="jira",
            )

    if slack_configs:
        slack_connector = SlackConnector(
            tuple(item.to_destination() for item in slack_configs),
            environment=runtime_environment,
            transport=slack_transport,
        )
        for slack_config in slack_configs:
            registry.register(
                slack_connector,
                slack_config.destination_id,
                display_name=slack_config.display_name,
                kind="alert",
                provider="slack",
            )


def _validate_labels(labels: tuple[str, ...]) -> None:
    if len(labels) > 20:
        raise ValueError("provider destinations accept at most 20 static labels")
    if any(re.fullmatch(_LABEL_PATTERN, label) is None for label in labels):
        raise ValueError("provider labels must be bounded static identifiers")
