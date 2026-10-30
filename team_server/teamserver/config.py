"""Runtime configuration, read once from the environment and validated.

Required: PORT, PUBLIC_BASE_URL, MONGODB_URI, MONGODB_DATABASE,
GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET. TEAM_NAME and
INITIAL_ADMIN_GITHUB_ID only matter before the team exists (bootstrap).
TEST_SESSION_KEY_SHA256 turns on the staging test sign-in
(`staging_sessions`); it is refused unless RAILWAY_ENVIRONMENT_NAME is
`staging`.
TEAM_SERVER_MESSAGING (1/true/yes/on) only seeds the team's messaging
switch: started with it on and nothing stored yet, the switch is stored on,
once. After that an administrator turns messaging on and off. The TEAM_SERVER_* file settings (`messaging_ports.ATTACHMENT_SETTINGS`)
may only lower the contract's defaults.

Problems are reported by variable NAME only, never with a value, because
several of these values are credentials.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional
from urllib.parse import urlsplit

from teamserver.messaging_ports import MESSAGING_FLAG

if TYPE_CHECKING:
    from teamserver.attachments import AttachmentSettings

REQUIRED = ("PORT", "PUBLIC_BASE_URL", "MONGODB_URI", "MONGODB_DATABASE",
            "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET")
TEAM_NAME_MAX = 100
_DB_NAME = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
# An explicit local-development exception: a plain-HTTP loopback base URL is
# accepted only when this is set. Never set in Railway.
DEV_INSECURE_FLAG = "TEAM_SERVER_DEV_INSECURE"
# The staging test sign-in: the SHA-256 of its key, only ever in staging.
TEST_KEY_DIGEST = "TEST_SESSION_KEY_SHA256"
ENVIRONMENT_NAME = "RAILWAY_ENVIRONMENT_NAME"
STAGING = "staging"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Config:
    port: int
    public_base_url: str
    mongodb_uri: str
    mongodb_database: str
    github_client_id: str
    github_client_secret: str
    team_name: Optional[str]
    initial_admin_github_id: Optional[int]
    dev_insecure: bool = False
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    environment_name: Optional[str] = None
    test_session_key_sha256: Optional[str] = None
    # Seeds the stored switch once (messaging.seed_switch); never read after.
    messaging_enabled: bool = False
    # None: the contract's defaults (attachments.DEFAULT_SETTINGS).
    attachment_settings: Optional["AttachmentSettings"] = None

    @property
    def secure(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def origin(self) -> str:
        return self.public_base_url

    @property
    def callback_url(self) -> str:
        return self.public_base_url + "/auth/github/callback"

    @property
    def test_sessions_enabled(self) -> bool:
        """Both conditions, checked here as well as in `load`, so a Config
        built any other way cannot open the staging test sign-in either."""
        return (self.environment_name == STAGING
                and bool(self.test_session_key_sha256))

    @property
    def bootstrap_configured(self) -> bool:
        return self.initial_admin_github_id is not None and bool(self.team_name)

    def __repr__(self) -> str:  # never print credentials
        return (f"Config(public_base_url={self.public_base_url!r}, "
                f"mongodb_database={self.mongodb_database!r}, "
                f"bootstrap_configured={self.bootstrap_configured})")

    __str__ = __repr__


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url_problem(value: str, dev_insecure: bool) -> Optional[str]:
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return "PUBLIC_BASE_URL must be an absolute https URL"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return "PUBLIC_BASE_URL must be an origin with no path, query or credentials"
    if parts.scheme == "http" and not (dev_insecure and _is_loopback_host(parts.hostname)):
        return ("PUBLIC_BASE_URL must use https (plain http is allowed only for a "
                f"loopback host with {DEV_INSECURE_FLAG}=1)")
    return None


def load(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    problems: list[str] = []
    get = lambda k: (env.get(k) or "").strip()  # noqa: E731
    for name in REQUIRED:
        if not get(name):
            problems.append(f"{name} is missing")
    dev_insecure = get(DEV_INSECURE_FLAG).lower() in ("1", "true", "yes")

    port = 0
    if get("PORT"):
        try:
            port = int(get("PORT"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            problems.append("PORT must be a number from 1 to 65535")
    base = get("PUBLIC_BASE_URL").rstrip("/")
    if base:
        p = _base_url_problem(base, dev_insecure)
        if p:
            problems.append(p)
    uri = get("MONGODB_URI")
    if uri and not uri.startswith(("mongodb://", "mongodb+srv://")):
        problems.append("MONGODB_URI must be a mongodb:// or mongodb+srv:// URI")
    dbname = get("MONGODB_DATABASE")
    if dbname and not _DB_NAME.match(dbname):
        problems.append("MONGODB_DATABASE has characters MongoDB does not allow")

    admin_id: Optional[int] = None
    if get("INITIAL_ADMIN_GITHUB_ID"):
        try:
            admin_id = int(get("INITIAL_ADMIN_GITHUB_ID"))
            if admin_id <= 0:
                raise ValueError
        except ValueError:
            problems.append("INITIAL_ADMIN_GITHUB_ID must be a numeric GitHub user ID")
    team_name = get("TEAM_NAME") or None
    if team_name and len(team_name) > TEAM_NAME_MAX:
        problems.append(f"TEAM_NAME is longer than {TEAM_NAME_MAX} characters")
    if admin_id is not None and not team_name:
        problems.append("TEAM_NAME is required with INITIAL_ADMIN_GITHUB_ID")
    environment = get(ENVIRONMENT_NAME) or None
    test_digest = get(TEST_KEY_DIGEST).lower() or None
    if test_digest and environment != STAGING:
        problems.append(f"{TEST_KEY_DIGEST} is set outside the {STAGING} environment")
    elif test_digest and not _SHA256_HEX.match(test_digest):
        problems.append(f"{TEST_KEY_DIGEST} must be a SHA-256 hex digest")
    # Imported here: attachments -> sessions -> team imports this module.
    from teamserver.attachments import AttachmentSettings
    attachment_settings = None
    try:
        attachment_settings = AttachmentSettings.from_env(env)
    except ValueError as e:
        problems.extend(str(e).split("; "))
    if problems:
        raise ConfigError(problems)
    return Config(
        port=port, public_base_url=base, mongodb_uri=uri,
        mongodb_database=dbname, github_client_id=get("GITHUB_CLIENT_ID"),
        github_client_secret=get("GITHUB_CLIENT_SECRET"), team_name=team_name,
        initial_admin_github_id=admin_id, dev_insecure=dev_insecure,
        github_api_url=get("GITHUB_API_URL") or "https://api.github.com",
        github_web_url=get("GITHUB_WEB_URL") or "https://github.com",
        environment_name=environment, test_session_key_sha256=test_digest,
        messaging_enabled=get(MESSAGING_FLAG).lower() in ("1", "true", "yes", "on"),
        attachment_settings=attachment_settings,
    )
