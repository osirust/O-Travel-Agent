"""Copy this checkout to the isolated NVMe WSL build tree without copying secrets."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside-wsl", action="store_true")
    args = parser.parse_args()
    if sys.platform == "win32":
        script = "/mnt/" + str(Path(__file__).resolve())[0].lower() + str(Path(__file__).resolve())[2:].replace("\\", "/")
        return subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "python3", script, "--inside-wsl"], check=False).returncode
    if not args.inside_wsl:
        raise SystemExit("Use the explicit isolated WSL target.")
    source = Path("/mnt/e/work/aegml").resolve()
    dest = Path("/home/aegml/projects/otravel").resolve()
    assert str(dest) == "/home/aegml/projects/otravel" and (dest / ".git").is_dir()
    files = ["pyproject.toml", "requirements.lock", "Dockerfile", "docker-compose.yml", ".gitignore", ".dockerignore", ".env.example", "README.md", "PROJECT_STATE.md", "AGENTS.md"]
    for name in files:
        src = source / name
        if src.is_file():
            shutil.copyfile(src, dest / name)
    count = 0
    for name in ("otravel", "scripts", "tests", "migrations", "docs"):
        for src in (source / name).rglob("*"):
            if not src.is_file() or src.is_symlink() or any(p in {"__pycache__", ".pytest_cache", ".local", ".venv"} for p in src.parts):
                continue
            if src.name.startswith(".env") or src.suffix in {".pyc", ".pem", ".key"}:
                continue
            target = dest / src.relative_to(source)
            assert target.resolve().is_relative_to(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            count += 1
    exclude = dest / ".git/info/exclude"
    text = exclude.read_text() if exclude.exists() else ""
    for entry in (".env", ".env.*", ".local/"):
        if entry not in text.splitlines():
            text += "\n" + entry
    exclude.write_text(text + "\n")
    print(f"Copied {count} code/document files; local .env preserved and excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
