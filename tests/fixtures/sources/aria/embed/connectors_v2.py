from dataclasses import dataclass, field
@dataclass
class ConnectorDefinition:
    id: str; name: str; provider: str; category: str; auth_type: str; scopes: list = field(default_factory=list); risk_level: str = "medium"; tools: list = field(default_factory=list)
CONNECTORS_CATALOG: dict[str, ConnectorDefinition] = {
    "google_calendar": ConnectorDefinition(id="google_calendar", name="Google Calendar", provider="google", category="scheduling", auth_type="oauth2", scopes=["calendar"], risk_level="medium", tools=["check_availability", "book_appointment"]),
    "slack": ConnectorDefinition(id="slack", name="Slack", provider="slack", category="communication", auth_type="oauth2", tools=["handoff_to_human"]),
    "teams": ConnectorDefinition(id="teams", name="Microsoft Teams", provider="microsoft", category="communication", auth_type="oauth2", scopes=field(default_factory=list)),
}
