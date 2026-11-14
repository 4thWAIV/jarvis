"""Every setting a person makes, as data: the one list Settings, the brain's
settings tools, the one-time copy from `.env` and the environment registry
(`env_names.py`) are all built from.

A setting lives in one of three places (`store`):

* ``record``: a field of its area's record, ``/settings/<area>.settings``
  (records.py), read once the records are open;
* ``start``: the start file (`start_settings.py`), the few choices needed
  before the records are open (the address, the port, HTTPS, whether the
  brain starts, the page's extra origins, the docs console and the
  engine's memory budget);
* ``secret``: the Keychain (team_client's store, service ``JARVIS
  secrets``), never a record.

``control`` says whether Settings draws it from this list (``generic``,
`SettingsFields.tsx`) or a section draws it with a control of its own
(``own``). ``target`` is its Settings target in the UI contract (the
``settingsItem`` key), ``page_only`` that only his click on Settings changes
it (no acting tool), ``restart`` that it takes effect when JARVIS next
starts. ``env`` is the name `.env` used to hold it, read once, by
`settings_import.py`, and never again.

Pure data: nothing here reads a file, a record or the environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

# The areas, in Settings' order; each is a section of Settings.
AREAS = ("you", "voice", "brains", "agents", "projects", "knowledge", "safety", "team",
         "server")

Choices = Union[tuple, Callable[[], tuple], None]


@dataclass(frozen=True)
class Spec:
    key: str
    area: str
    kind: str                       # bool, int, float, text, choice, path, paths, origins, secret
    default: Any
    env: Optional[str] = None       # the `.env` name it was held under, for the one-time copy
    store: str = "record"           # record, start, secret
    field: Optional[str] = None     # the record's field, when not the key
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Choices = None
    max_chars: int = 500
    optional: bool = False          # a number that may be left empty (None)
    restart: bool = False
    page_only: bool = False
    control: str = "generic"        # generic, own
    target: str = ""                # the settingsItem key in the UI contract
    tools: tuple = ("jarvis_settings", "set_jarvis_setting")

    @property
    def record_field(self) -> str:
        return self.field or self.key

    def choice_values(self) -> tuple:
        c = self.choices
        return tuple(c() if callable(c) else (c or ()))


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _local_voices() -> tuple:
    from jarvis import local_tts
    return tuple(local_tts.VOICES)


def _honorifics() -> tuple:
    from jarvis import spoken_words
    return tuple(spoken_words.HONORIFICS)


def _s(key: str, area: str, kind: str, default: Any, **kw) -> Spec:
    kw.setdefault("target", _camel(key))
    return Spec(key, area, kind, default, **kw)


SPECS: tuple[Spec, ...] = (
    # You
    _s("user_name", "you", "text", "", env="USER_NAME", max_chars=64, control="own",
       target="name"),
    _s("honorific", "you", "choice", "sir", env="HONORIFIC", choices=_honorifics,
       control="own", target="honorific"),
    _s("weather_location_label", "you", "text", "", env="WEATHER_LOCATION_LABEL",
       max_chars=80),
    _s("weather_latitude", "you", "float", None, env="WEATHER_LATITUDE", minimum=-90,
       maximum=90, optional=True),
    _s("weather_longitude", "you", "float", None, env="WEATHER_LONGITUDE", minimum=-180,
       maximum=180, optional=True),
    _s("weather_unit", "you", "choice", "fahrenheit", env="WEATHER_UNIT",
       choices=("fahrenheit", "celsius")),
    _s("default_browser", "you", "choice", "chrome", env="JARVIS_DEFAULT_BROWSER",
       choices=("chrome", "firefox")),
    # Voice and language
    _s("fish_api_key", "voice", "secret", "", env="FISH_API_KEY", store="secret",
       control="own", target="fishKey", page_only=True),
    _s("fish_voice_id", "voice", "text", "612b878b113047d9a770c069c8b4fdfe",
       env="FISH_VOICE_ID", control="own", target="voiceId"),
    _s("tts_provider", "voice", "choice", "fish", env="JARVIS_TTS_PROVIDER",
       choices=("fish", "local"), control="own", target="voiceOutputProvider"),
    _s("local_voice", "voice", "choice", "george", env="JARVIS_LOCAL_VOICE",
       choices=_local_voices, control="own", target="localVoice"),
    _s("mute_mic_during_speech", "voice", "bool", False, env="JARVIS_MUTE_MIC_DURING_SPEECH"),
    _s("stt_model_dir", "voice", "path", "", env="JARVIS_STT_MODEL_DIR", restart=True),
    # Brains
    _s("brain_effort", "brains", "choice", "low", env="JARVIS_BRAIN_EFFORT",
       choices=("low", "medium", "high"), restart=True),
    _s("brain_turn_timeout", "brains", "float", 90.0, env="JARVIS_BRAIN_TURN_TIMEOUT",
       minimum=10, maximum=3600, restart=True),
    _s("brain_context_budget", "brains", "int", 900000, env="JARVIS_BRAIN_CONTEXT_BUDGET",
       minimum=50000, maximum=2000000, restart=True),
    _s("claude_path", "brains", "path", "", env="JARVIS_CLAUDE_PATH", restart=True,
       page_only=True),
    _s("codex_path", "brains", "path", "", env="JARVIS_CODEX_PATH", restart=True,
       page_only=True),
    _s("usage_poll", "brains", "bool", True, env="JARVIS_USAGE_POLL"),
    # Agents
    _s("run_timeout_sec", "agents", "float", 6 * 3600.0, env="JARVIS_RUN_TIMEOUT_SEC",
       minimum=60, maximum=24 * 3600),
    _s("run_idle_sec", "agents", "float", 30 * 60.0, env="JARVIS_RUN_IDLE_SEC",
       minimum=30, maximum=24 * 3600),
    _s("skip_permissions", "agents", "bool", True, env="JARVIS_SKIP_PERMISSIONS"),
    _s("steer_cancel_window", "agents", "float", 2.0, env="JARVIS_STEER_CANCEL_WINDOW",
       minimum=0, maximum=30),
    _s("claude_config_dirs", "agents", "paths", [], env="JARVIS_CLAUDE_CONFIG_DIRS"),
    _s("codex_sessions_home", "agents", "path", "", env="JARVIS_CODEX_SESSIONS_HOME"),
    _s("watch_interval", "agents", "float", 1.0, env="JARVIS_WATCH_INTERVAL", minimum=0.2,
       maximum=60, restart=True),
    _s("specs_poll", "agents", "float", 2.0, env="JARVIS_SPECS_POLL", minimum=0.05,
       maximum=600),
    # Projects
    _s("projects_dir", "projects", "path", "", env="JARVIS_PROJECTS_DIR"),
    _s("project_roots", "projects", "paths", [], env="JARVIS_PROJECT_ROOTS"),
    _s("scan_budget", "projects", "float", 20.0, env="JARVIS_SCAN_BUDGET", minimum=0.1,
       maximum=600),
    _s("scan_cache", "projects", "float", 300.0, env="JARVIS_SCAN_CACHE", minimum=0,
       maximum=86400),
    # Knowledge
    _s("knowledge_enabled", "knowledge", "bool", True, env="JARVIS_KNOWLEDGE",
       field="enabled", control="own", target="knowledgeEnabled",
       tools=("knowledge_status", "set_knowledge_settings")),
    _s("run_context", "knowledge", "bool", False, env="JARVIS_RUN_CONTEXT"),
    _s("folders", "knowledge", "bool", True, env="JARVIS_FOLDERS", restart=True),
    _s("folder_discovery", "knowledge", "bool", True, env="JARVIS_FOLDER_DISCOVERY",
       restart=True),
    _s("knowledge_memory_mb", "knowledge", "int", None, env="JARVIS_KNOWLEDGE_MEMORY_MB",
       store="start", minimum=64, maximum=8192, optional=True, restart=True),
    # Safety
    _s("refuse_untrusted_actions", "safety", "bool", False,
       env="JARVIS_REFUSE_UNTRUSTED_ACTIONS"),
    # Team
    _s("team_dev_servers", "team", "origins", [], env="JARVIS_TEAM_DEV_SERVERS"),
    # Server (the start file)
    _s("host", "server", "text", "127.0.0.1", store="start", max_chars=253, restart=True,
       page_only=True, target="serverHost"),
    _s("port", "server", "int", 8340, store="start", minimum=1024, maximum=65535,
       restart=True, target="serverPortChoice"),
    _s("https", "server", "bool", True, store="start", restart=True, target="serverHttps"),
    _s("brain_autostart", "server", "bool", True, env="JARVIS_BRAIN_AUTOSTART",
       store="start", restart=True),
    _s("allowed_origins", "server", "origins", [], env="JARVIS_ALLOWED_ORIGINS",
       store="start", page_only=True),
    _s("debug_docs", "server", "bool", False, env="JARVIS_DEBUG_DOCS", store="start",
       restart=True),
)

BY_KEY: dict[str, Spec] = {s.key: s for s in SPECS}
BY_ENV: dict[str, Spec] = {s.env: s for s in SPECS if s.env}

# Settings that were `.env` names and are kept by a control and store of their
# own (brains.json, engines.json), copied there once by settings_import:
# env name -> (the Settings target, the brain tools that read and change it).
ELSEWHERE: dict[str, tuple[str, tuple]] = {
    "JARVIS_BRAIN_PROVIDER": ("brainCard", ("brains_settings", "set_brain")),
    "JARVIS_BRAIN_MODEL": ("brainCard", ("brains_settings", "set_brain")),
    "JARVIS_RUN_MODEL": ("engineRun", ("engine_settings", "set_engine_setting")),
}


def spec(key: str) -> Spec:
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(f"no setting is called {key}") from None


def in_area(area: str) -> list[Spec]:
    return [s for s in SPECS if s.area == area]

