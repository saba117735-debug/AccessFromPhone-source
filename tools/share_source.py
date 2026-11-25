from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FOLDERS = ("src", "android", "tests", "docs", "tools", "packaging")
ROOT_FILES = (
    "pyproject.toml", "START.bat", "run.ps1", "README.md", "INSTALL.txt",
    "READ-ME-FIRST.txt", ".gitignore",
)
EXCLUDED_DIRS = {
    ".venv", "venv", ".git", ".gradle", ".kotlin", ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__",
    "build", "dist", "node_modules", "data", "test-results", "playwright-report",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".apk", ".aab", ".exe", ".msi", ".zip",
    ".jks", ".keystore", ".pem", ".key", ".p12", ".pfx",
    ".db", ".sqlite", ".sqlite3", ".log",
}
SHARING_NOTES = """AccessFromPhone - source package

Windows setup:
1. Install Python 3.11 or newer, with Add Python to PATH enabled.
2. Extract this entire ZIP into a folder.
3. Double-click START.bat. Internet is needed to install dependencies.

Dependencies are declared in pyproject.toml. START.bat creates .venv and
installs them automatically on first run. For development/tests, run:
  .venv\\Scripts\\python -m pip install -e ".[dev]"

Android development also requires Android Studio and its Android SDK.
Open android/ in Android Studio, or run .\\android\\build.ps1 in PowerShell.
The Gradle wrapper downloads Gradle and build dependencies when needed.

No installed environments, build outputs, personal workspace files, access
tokens, certificates, databases, or Firebase configuration are included.
Push notifications are disabled in this copy. To enable them, configure your
own Firebase project and credentials, including android/app/google-services.json.
Never share Firebase service-account keys or other private credentials.

The AI provider in config.yaml requires your own ANTHROPIC_API_KEY environment
variable. Alternatively configure a supported local provider and install its
model separately. Read README.md for the remaining configuration options.

To create another source package after editing, run:
  .venv\\Scripts\\python tools\\share_source.py
"""


def excluded(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.is_symlink()
        or path.is_junction()
        if hasattr(path, "is_junction") else path.is_symlink()
    ) or (
        name in EXCLUDED_DIRS
        or name.endswith(".egg-info")
        or name.startswith(".env")
        or name in {"local.properties", "google-services.json", "token.txt"}
        or name.startswith("firebase-")
        or "service-account" in name
        or "service_account" in name
        or path.suffix.lower() in EXCLUDED_SUFFIXES
    )


def source_files(folder: Path):
    for path in sorted(folder.iterdir()):
        if excluded(path):
            continue
        if path.is_dir():
            yield from source_files(path)
        elif path.is_file():
            yield path


def main() -> None:
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["push"] = {"enabled": False}
    config["agent"]["allowed_folders"] = ["workspace"]
    config["agent"]["read_only_folders"] = []
    config["relay"] = {"enabled": False, "url": "http://localhost:9000"}
    config["server"]["public_url"] = "auto"
    config["server"]["token_file"] = "data/token.txt"
    config["storage"] = {
        "results_dir": "data/results", "snapshots_dir": "data/snapshots",
        "link_expiry_minutes": 60, "database": "data/app.db",
    }
    config["logging"]["audit_file"] = "data/audit.log"
    output_dir = ROOT / "dist"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / f"AccessFromPhone-source-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ROOT_FILES:
            archive.write(ROOT / name, name)
        for name in SOURCE_FOLDERS:
            for path in source_files(ROOT / name):
                archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr("config.yaml", yaml.safe_dump(config, sort_keys=False))
        archive.writestr("SHARING-README.txt", SHARING_NOTES)
        archive.writestr("workspace/welcome.txt", "Your shared workspace starts here.\n")
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        members = set(archive.namelist())
        required = {
            "pyproject.toml", "START.bat", "config.yaml", "src/handoff/__main__.py",
            "android/gradlew.bat", "android/gradle/wrapper/gradle-wrapper.jar",
            "android/gradle/wrapper/gradle-wrapper.properties",
        }
        if not required.issubset(members):
            raise RuntimeError(f"Missing required source files: {required - members}")
        for member in members:
            if any(excluded(Path(part)) for part in Path(member).parts):
                raise RuntimeError(f"Excluded file found in ZIP: {member}")
        packaged_config = yaml.safe_load(archive.read("config.yaml"))
        if packaged_config["push"] != {"enabled": False}:
            raise RuntimeError("Packaged push settings were not sanitized")
        print(f"Created: {output}")
        print(f"Size: {output.stat().st_size / (1024 * 1024):.2f} MB")
        print(f"Files: {len(members)}; ZIP integrity and exclusion checks passed")


if __name__ == "__main__":
    main()