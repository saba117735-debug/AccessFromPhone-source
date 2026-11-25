"""Single source of configuration.

Precedence, lowest to highest: built-in defaults, config.yaml, environment
variables (HANDOFF_SECTION__KEY), explicit init values.

Relative paths in the file are resolved against the folder that holds it, so
the project can be moved without editing paths.
"""
from __future__ import annotations

import os
import re
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .paths import is_inside

_BASE_DIR: ContextVar[Path | None] = ContextVar("handoff_base_dir", default=None)


def _abs(path: Path) -> Path:
    base = _BASE_DIR.get() or Path.cwd()
    return (path if path.is_absolute() else base / path).resolve()


class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    SEND = "send"
    DEPLOY = "deploy"
    MOVE = "move"
    COPY = "copy"  # reads the source (which may be read-only), writes the destination
    RUN = "run"
    POWER = "power"  # lock, sleep, restart or shut down this PC
    APP = "app"  # open an app, or read and answer Teams chats, on this PC

    @property
    def changes_files(self) -> bool:
        """Only these need a writable folder. Reading, sending and copying from it do not."""
        return self in (Action.WRITE, Action.DELETE, Action.DEPLOY, Action.MOVE, Action.RUN)


class ConnectivityConfig(BaseModel):
    """How phones find and reach this PC."""

    lan_address: Literal["ip", "name"] = "ip"  # ip = the QR holds the Wi-Fi address; name = this PC's .local name, which survives an address change
    mdns: bool = True  # announce this PC on the local network so its .local name can be found (needs the optional "zeroconf" package)
    anywhere: Literal["auto", "off"] = "auto"  # auto = offer access from anywhere through Tailscale when it is installed and signed in
    anywhere_port: int = Field(8443, ge=1, le=65535)  # the private listener that only the Tailscale network can reach
    tailscale_path: str = ""  # where tailscale.exe is, if it is not found by itself

    @field_validator("anywhere", mode="before")
    @classmethod
    def _yaml_off(cls, value):
        """YAML reads a bare `off` / `on` as false / true. Mean what the person typed."""
        return {False: "off", True: "auto"}.get(value, value)


class RelayConfig(BaseModel):
    """Reach this PC from any network through YOUR server (the relay), with nothing to install on the phone or the router."""

    enabled: bool = False
    url: str = "http://localhost:9000"  # your relay, e.g. https://xyz.com. Phones then open https://<this-pc's-id>.xyz.com
    register_key: str = ""  # a secret that lets this PC register. Put it in the HANDOFF_RELAY__REGISTER_KEY environment variable, not here

    @field_validator("url")
    @classmethod
    def _no_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")


class PushConfig(BaseModel):
    """Notifications that reach a phone that is locked or has the page closed ("Plan ready: tap to approve", "Job finished")."""

    enabled: bool = False
    # The first four are about a request. The pc_* ones tell the phone BEFORE this PC goes away (it cannot tell the phone afterwards).
    notify_on: list[Literal["awaiting", "needs_access", "done", "failed", "pc_sleep", "pc_restart", "pc_shutdown", "pc_closing", "pc_battery", "pc_plugged", "pc_unplugged"]] = [
        "awaiting", "needs_access", "done", "failed", "pc_sleep", "pc_restart", "pc_shutdown", "pc_closing", "pc_battery", "pc_plugged", "pc_unplugged"]
    battery_low_percent: int = Field(15, ge=5, le=50)  # "PC battery is low" at this level when unplugged (and once more at 5%)
    show_details: bool = False  # false = the notification never says what the request was or what it found (it passes through Google/Apple)
    min_seconds: int = Field(15, ge=0)  # "done" is sent only for a request that took at least this long, so an instant answer does not buzz
    credentials_file: Path | None = None  # a .env file to read the push keys from, so they need not be copied (VAPID_* and FIREBASE_SERVICE_ACCOUNT_PATH)
    vapid_public_key: str = ""  # browser push. The public half is not secret
    vapid_private_key: str = ""  # the secret half: put it in the HANDOFF_PUSH__VAPID_PRIVATE_KEY environment variable, not here
    vapid_subject: str = ""  # mailto:you@example.com, so a push service can reach you if something misbehaves
    firebase_service_account_file: Path | None = None  # Android app push (Firebase). The path to the service-account JSON: the file itself stays out of this folder
    ca_file: Path | None = None  # extra certificates to trust, if a work network re-signs the connection and Windows alone is not enough

    @field_validator("credentials_file", "firebase_service_account_file", "ca_file", mode="before")
    @classmethod
    def _path(cls, value):
        return None if value in (None, "") else _abs(Path(value))


class AppsConfig(BaseModel):
    """Opening apps and using Teams from the phone. Reading or answering Teams always asks; opening an app asks unless you turn that off."""

    enabled: bool = True  # open any app in the Start menu
    teams: bool = True  # read and answer Microsoft Teams chats by driving its window
    approve_open: bool = True  # ask before opening an app


class PowerConfig(BaseModel):
    remote_actions: list[Literal["lock", "screen_off", "sleep", "hibernate", "restart", "shutdown"]] = ["lock", "screen_off", "sleep"]  # what the phone may ask this PC to do (each one still needs your approval)
    keep_awake: bool = True  # stop Windows sleeping while the app runs, so the phone can always reach the PC (the screen may still turn off)


class UnlockConfig(BaseModel):
    """Unlocking this PC from the phone. It only works once `python -m handoff.unlock.setup install` has been run at the PC."""

    agent_port: int = Field(8769, ge=1, le=65535)  # where the unlock helper listens (on this PC only)
    wait_seconds: int = Field(10, ge=3, le=60)  # how long to wait for the lock screen to go away after the sign-in is typed


class AccountsConfig(BaseModel):
    """Who may use this PC from a phone: an owner who sets it up on the PC, and phones paired one at a time."""

    enabled: bool = True  # false = the old single shared token in data/token.txt (for testing only)
    sign_in: Literal["auto", "windows", "password"] = "auto"  # auto = your Windows user name and password on Windows; password = an app password made at setup
    file: Path = Field(Path("data/accounts.json"), validate_default=True)
    admin_port: int = Field(8766, ge=1, le=65535)  # the PC's own page: http on 127.0.0.1 only, never reachable from the network
    pairing_minutes: int = Field(2, ge=1, le=30)  # how long a QR code / pairing code works, and for one phone only
    max_devices: int = Field(10, ge=1, le=100)
    forget_after_days: int = Field(0, ge=0, le=3650)  # remove phones not seen for this long (0 = never): leftovers of cleared browsers and reinstalls
    session_minutes: int = Field(60, ge=5, le=1440)  # how long the PC page stays signed in

    @field_validator("file")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return _abs(value)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    public_url: str = "auto"  # "auto" = this PC's current Wi-Fi address, found again at every start. Or a fixed address such as http://10.0.0.5:8765
    token_file: Path = Field(Path("data/token.txt"), validate_default=True)
    https: bool = False  # serve over https with a self-signed certificate (needed for voice input on a phone)
    tls_dir: Path = Field(Path("data/tls"), validate_default=True)
    open_connect_page: bool = True  # on start, show a big "Connect your phone" QR page on the PC; false = QR in the terminal only

    @property
    def public_url_now(self) -> str:
        """public_url, with "auto" turned into this PC's address on the network right now."""
        if self.public_url.strip().lower() in ("", "auto"):
            from .connectivity import lan_ip

            return f"{'https' if self.https else 'http'}://{lan_ip()}:{self.port}"
        return self.public_url

    @property
    def link_base(self) -> str:
        """The public address with the scheme that matches `https`."""
        rest = self.public_url_now.split("://", 1)[-1]
        return f"{'https' if self.https else 'http'}://{rest}"

    @field_validator("token_file", "tls_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return _abs(value)

    @field_validator("public_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class AgentConfig(BaseModel):
    allowed_folders: list[Path]  # can be read, searched and (with approval) changed
    read_only_folders: list[Path] = Field(default_factory=list)  # read and search only
    max_job_minutes: int = Field(30, ge=1)
    max_download_mb: int = Field(0, ge=0)  # largest file the phone may fetch; 0 = no limit (files are streamed, so any size works)
    # When a request needs a folder that is not listed, ask the user on the phone (read only, can be taken back).
    # false = only the folders listed here are ever available.
    ask_for_access: bool = True
    # Edit files by hand from the phone. Saving in a folder that is not in allowed_folders needs the user to allow
    # changes there first (revocable, and never inside a read_only_folders entry). false = only allowed_folders.
    allow_edit_grants: bool = True
    max_edit_kb: int = Field(512, ge=16)  # the largest file the phone's editor lets you change
    max_view_kb: int = Field(4096, ge=64)  # the largest text file it will show (bigger ones: download or ask the AI)
    max_image_mb: int = Field(25, ge=1)  # the largest edited picture the phone may save back
    protected_folders: list[Path] = Field(default_factory=list)  # never available, even if asked: added to the built-in list

    @field_validator("allowed_folders")
    @classmethod
    def _folders_exist(cls, folders: list[Path]) -> list[Path]:
        if not folders:
            raise ValueError("allowed_folders must list at least one folder")
        resolved = [_abs(f) for f in folders]
        for folder in resolved:
            if not folder.is_dir():
                raise ValueError(f"allowed folder does not exist: {folder}")
        return resolved

    @field_validator("read_only_folders")
    @classmethod
    def _read_only_exist(cls, folders: list[Path]) -> list[Path]:
        resolved = [_abs(f) for f in folders]
        for folder in resolved:
            if not folder.is_dir():
                raise ValueError(f"read-only folder does not exist: {folder}")
        return resolved

    @model_validator(mode="after")
    def _no_overlap(self) -> "AgentConfig":
        for read_only in self.read_only_folders:
            for writable in self.allowed_folders:
                if is_inside(read_only, writable):
                    raise ValueError(
                        f"read-only folder {read_only} is inside the writable folder "
                        f"{writable}; remove one of them"
                    )
        return self


class ApprovalConfig(BaseModel):
    # "write" also covers move and copy
    require_for: list[Literal["write", "delete", "send", "deploy", "run", "power"]] = [
        "write",
        "delete",
        "send",
        "deploy",
        "run",
        "power",
    ]
    timeout_minutes: int = Field(15, ge=1)

    @model_validator(mode="after")
    def _commands_always_ask(self) -> "ApprovalConfig":
        for must in ("run", "power"):  # a forgotten entry must never let commands run, or the PC go to sleep, unasked
            if must not in self.require_for:
                self.require_for = [*self.require_for, must]
        return self


# A speed bump, NOT a sandbox. It stops the careless and the obvious, and it is easy to get around
# (encoded or split-up commands, other tools that download or run things). The real protections are:
# commands are off by default, every command waits for the user's approval with the whole command
# shown, and it starts inside a writable folder (which is where it begins, not a wall around it).
DEFAULT_DENY = [
    r"(^|[;&|(]\s*)format(\.com)?\s+[A-Za-z]:",  # the disk command, not Get-Date -Format or Format-Table
    r"\bFormat-Volume\b",
    r"\bdiskpart\b",
    r"\b(shutdown|Restart-Computer|Stop-Computer)\b",
    r"\breg(\.exe)?\s+(add|delete|import)\b",
    r"\bnet\s+(user|localgroup)\b",
    r"\bSet-ExecutionPolicy\b",
    r"\b(iex|Invoke-Expression)\b",
    r"\b(curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\b.*\|\s*(iex|sh|bash|powershell|pwsh)\b",
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+/",
    r"\bRemove-Item\b.*-Recurse.*\s[A-Za-z]:[/\x5c]?\s*$",
    r"\b(bcdedit|vssadmin|cmdkey|mimikatz|schtasks\s+/create|Register-ScheduledTask)\b",
    r"\b(sc|sc\.exe)\s+(delete|stop|config)\b",
    r"(\.ssh|id_rsa|\.aws[/\x5c]credentials|lsass)",
]


class CommandsConfig(BaseModel):
    """Running PC commands. Off by default; each run always waits for approval."""

    enabled: bool = False
    timeout_seconds: int = Field(60, ge=1, le=3600)
    max_output_chars: int = Field(20_000, ge=100)
    allow_only: list[str] = Field(default_factory=list)  # if set, a command must start with one of these
    deny: list[str] = Field(default_factory=lambda: list(DEFAULT_DENY))

    @field_validator("deny")
    @classmethod
    def _valid_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"commands.deny has an invalid pattern {pattern!r}: {exc}") from None
        return patterns


class LlmConfig(BaseModel):
    # auto: local model if Ollama has it, otherwise demo commands. It never picks the hosted model.
    # anthropic: a hosted Claude model. Requests and any file text it reads leave this PC.
    provider: Literal["auto", "ollama", "anthropic", "demo"] = "auto"
    model: str = "qwen2.5:7b"
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: int = Field(180, ge=5)  # a CPU-only PC can be slow
    max_rounds: int = Field(6, ge=1, le=20)
    temperature: float = Field(0.2, ge=0, le=2)
    keep_alive: str = "30m"  # how long Ollama keeps the model in memory; reloading it costs many seconds
    memory_turns: int = Field(4, ge=0, le=50)  # earlier requests the model is reminded of (each one costs reading time)
    # Speed on a PC without a graphics card. Reading text costs roughly a second per 30 tokens, writing about 5 to 10
    # tokens a second, so these keep both small. Nothing here makes an answer worse for a normal request.
    num_ctx: int = Field(4096, ge=1024)  # how much text the model holds at once. Too small silently cuts the prompt
    max_output_tokens: int = Field(700, ge=64)  # stop a runaway answer; a cut-off change is never applied
    file_chars: int = Field(3500, ge=500)  # how much of a file is handed to the model (about 900 tokens)
    # CPU threads for Ollama. "auto" leaves two cores for Windows and everything else (on a 12-thread laptop that is 10,
    # which measured about 60% faster at reading and 50% faster at writing than Ollama's own choice). A number sets it;
    # null lets Ollama choose.
    num_thread: int | Literal["auto"] | None = "auto"
    hosted_model: str = "claude-sonnet-5-5"
    hosted_url: str = "https://api.anthropic.com"
    api_key_env: str = "ANTHROPIC_API_KEY"  # name of the environment variable that holds the key
    max_tokens: int = Field(2048, ge=64)

    @field_validator("base_url", "hosted_url")
    @classmethod
    def _no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class StorageConfig(BaseModel):
    results_dir: Path
    snapshots_dir: Path
    link_expiry_minutes: int = Field(60, ge=1)
    database: Path = Field(Path("data/app.db"), validate_default=True)  # one SQLite file: accounts, phones, chats, audit, allowed folders
    grants_file: Path = Field(Path("data/grants.json"), validate_default=True)  # only read once, to bring an older list into the database
    lock_data_folder: bool = True  # close the data folder to every other account on this PC (this Windows user and the system keep it)

    @field_validator("results_dir", "snapshots_dir", "grants_file", "database")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return _abs(value)


class LoggingConfig(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    audit_file: Path

    @field_validator("audit_file")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return _abs(value)


class AccessConfig(BaseModel):
    """Who may do what on this PC, and one person at a time. `enabled: false` switches the whole thing off (every paired phone acts freely, as before)."""

    enabled: bool = True
    lease_seconds: int = Field(60, ge=10, le=600)  # a phone that has not checked in this long has gone: its turn ends
    idle_minutes: int = Field(5, ge=1, le=240)  # nothing changed this long: the turn ends
    ask_seconds: int = Field(60, ge=10, le=600)  # how long the person using the PC has to answer "may I have a turn?"
    cooldown_seconds: int = Field(120, ge=0, le=3600)  # after "no, I'm still using it", how long before the same phone may ask again


class ScreenConfig(BaseModel):
    """Looking at this PC's screen from a phone: view only, one viewer at a time, only with the Owner's yes on this PC, always visible here."""

    enabled: bool = True
    idle_seconds: int = Field(20, ge=5, le=300)  # a phone that stops asking for pictures this long loses the viewing
    ask_seconds: int = Field(120, ge=15, le=900)  # how long a request waits for the Owner's answer
    banner: bool = True  # the small always-on-top window on this PC (asking, and viewing with a Stop button)
    control: bool = True  # false: no phone can ever move the mouse or type on this PC, whatever it is allowed
    admin_needs_yes: bool = False  # false: the Admin phone (the Owner's own) may look without asking each time; the banner and Stop still show on this PC
    audio: bool = True  # a phone that is looking may also listen to what this PC is playing (needs the audio package; off here: never)
    clipboard: bool = True  # a phone that controls this PC may paste text to it and read its clipboard (text only; every use is logged)
    panic_key: str = "ctrl+alt+shift+x"  # pressed on THIS PC's keyboard: stops looking and control at once, takes back control, and blocks it for a short while. "" turns it off
    release_after_seconds: int = Field(5, ge=2, le=60)  # a mouse button or key still held down with no word from the phone this long is let go (a dropped connection mid-drag)


class DailyConfig(BaseModel):
    """Everyday helpers on the phone: quick controls, an inbox, notes, reminders, watchers, a briefing, printing and snapshots. `enabled: false` removes all of them."""

    enabled: bool = True
    folder: Path = Field(Path("~/Documents/AccessFromPhone"), validate_default=True)  # holds Inbox (sent from the phone), To phone (files for the phone), Notes and Screenshots
    max_upload_mb: int = Field(100, ge=1, le=2048)  # the largest file the phone may send to this PC's inbox
    printing: bool = True  # print a file on this PC's default printer (each print is confirmed on the phone and logged)
    notifications: bool = False  # show this PC's Windows notifications on the phone. Off by default: they can hold private messages
    webcam: bool = False  # let the Admin phone take one photo with this PC's webcam (needs the opencv-python package). Off by default; each photo is logged and alerts every phone

    @field_validator("folder")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return _abs(Path(value).expanduser())


class LinksConfig(BaseModel):
    """Where the phone's Settings sends people for the privacy policy and for help. Empty = not shown. Must be https."""

    privacy_url: str = ""
    support_url: str = ""

    @field_validator("privacy_url", "support_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        value = value.strip()
        if value and not (value.startswith("https://") and " " not in value and len(value) <= 300):
            raise ValueError("links: must be an https:// address with no spaces")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANDOFF_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    # default_factory, not an instance: the default must be built while load_settings
    # has the config folder set, or its relative paths would resolve against the cwd.
    server: ServerConfig = Field(default_factory=ServerConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    connectivity: ConnectivityConfig = Field(default_factory=ConnectivityConfig)
    power: PowerConfig = Field(default_factory=PowerConfig)
    links: LinksConfig = Field(default_factory=LinksConfig)
    access: AccessConfig = Field(default_factory=AccessConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    daily: DailyConfig = Field(default_factory=DailyConfig)
    apps: AppsConfig = Field(default_factory=AppsConfig)
    unlock: UnlockConfig = Field(default_factory=UnlockConfig)
    relay: RelayConfig = Field(default_factory=RelayConfig)
    push: PushConfig = Field(default_factory=PushConfig)
    agent: AgentConfig
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    storage: StorageConfig
    logging: LoggingConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, YamlConfigSettingsSource(settings_cls))


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from `path`, $HANDOFF_CONFIG, or ./config.yaml."""
    config_path = Path(path or os.environ.get("HANDOFF_CONFIG", "config.yaml"))
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path.resolve()}")

    class _Loaded(Settings):
        model_config = SettingsConfigDict(
            {**Settings.model_config, "yaml_file": str(config_path)}
        )

    token = _BASE_DIR.set(config_path.resolve().parent)
    try:
        return _Loaded()
    finally:
        _BASE_DIR.reset(token)
