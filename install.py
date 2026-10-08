#!/usr/bin/env python3
"""Install links to one source tree without changing existing settings or PATH."""
from pathlib import Path
import argparse
import os


def install(home, root):
    source = root / "skills/agent-workflow"
    targets = [(home / ".local/bin/agent-workflow", root / "bin/agent-workflow")]
    for profile in (".codex", ".claude", ".claude-soonho", ".claude-alex"):
        if (home / profile).is_dir():
            targets.append((home / profile / "skills/agent-workflow", source))
    # Preflight the entire set before writing any links.
    for dest, src in targets:
        if (dest.exists() or dest.is_symlink()) and dest.resolve() != src.resolve():
            raise SystemExit(f"기존 항목과 충돌하여 설치 중단: {dest}")
    created = []
    try:
        for dest, src in targets:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_symlink():
                dest.symlink_to(src, target_is_directory=src.is_dir())
                created.append(dest)
            print(f"{dest} -> {src}")
    except OSError:
        for dest in reversed(created):
            dest.unlink()
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--home", type=Path, default=Path.home(), help="테스트/설치 대상 사용자 홈")
    args = p.parse_args()
    root = Path(__file__).resolve().parent
    os.chmod(root / "bin/agent-workflow", 0o755)
    install(args.home, root)
