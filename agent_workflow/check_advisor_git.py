"""Collect a bounded, local Git patch for check selection. Never read untracked file contents."""
from __future__ import annotations

import os
from pathlib import Path
import re
import selectors
import subprocess
import time

from .core import WorkflowError
from .runner import stop_group
from .session_notes import _filter_overrides

PATH_LIMIT = 64
PATH_BYTES = 512
DIFF_BYTES = 16 * 1024
FILE_DIFF_BYTES = 6 * 1024
_METADATA_BYTES = 512 * 1024

_SECRET_NAMES = {
    ".envrc", ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc", "kubeconfig",
    "credentials", "secrets", "auth.json", "token.json", "tokens.json", "application_default_credentials.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
_SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".secrets", "secrets", "credentials"}
_SENSITIVE_FILENAME = re.compile(
    r"(?:^|[._-])(?:secrets?|credentials?|service[-_]?account|private[-_]?key)(?:[._-]|$)"
)
_SECRET_TOKEN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[\w]{15,}|glpat-[\w-]{12,}|"
    r"sk-(?:ant-|proj-|svcacct-)?[\w-]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"(?:sk|rk)_live_[A-Za-z0-9]{16,}|xox[baprs]-[\w-]{10,}|AIza[\w-]{30,}|npm_[A-Za-z0-9]{20,}|"
    r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}|://[^/\s@:]+:[^/\s@]+@)"
)
_SECRET_LINE = re.compile(
    r"(?:[\w.-]*(?:password|passwd|pwd|secret(?:[_-]?key)?|token|credentials?|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization|cookie|database[_-]?url|connection[_-]?string))"
    r"""["']?\s*(?::|=)|\b(?:bearer|basic)\s+[\w+/=.-]{8,}|--(?:password|token|api-key)\s+""",
    re.I,
)


def _sensitive(path, excluded):
    name = path.name.lower()
    return (any(path == entry or entry in path.parents for entry in excluded)
            or any(part.lower() in _SECRET_DIRS for part in path.parts)
            or name in _SECRET_NAMES or name == ".env" or name.startswith(".env.")
            or name.endswith((".env", ".key", ".pem", ".p12", ".pfx", ".jks", ".keystore",
                              ".tfstate", ".tfstate.backup", ".kdbx"))
            or _SENSITIVE_FILENAME.search(name) or _SECRET_TOKEN.search(str(path)))


def _read_git(root, args, limit, deadline, *, allow_absent=False):
    """Bound subprocess output before decoding, also when Git emits a single huge line."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="", LC_ALL="C")
    cmd = ["git", "--no-pager", "--no-optional-locks", "--literal-pathspecs", "-C", str(root),
           "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
           "-c", "core.untrackedCache=false", "-c", "diff.autoRefreshIndex=false", *args]
    with subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=env, start_new_session=True) as proc:
        data = bytearray()
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(proc.stdout, selectors.EVENT_READ)
                while len(data) <= limit:
                    if not ready.select(max(0, deadline - time.monotonic())):
                        raise WorkflowError("추천용 Git 변경 조회 시간이 초과됐습니다.")
                    chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            if len(data) > limit:
                return bytes(data[:limit]), True
            code = proc.wait(timeout=max(0, deadline - time.monotonic()))
            if code and not (allow_absent and code == 1):
                raise WorkflowError("추천용 Git 변경을 읽을 수 없습니다. 작업 트리와 기준 커밋을 확인하세요.")
            return bytes(data), False
        except subprocess.TimeoutExpired as exc:
            raise WorkflowError("추천용 Git 변경 조회 시간이 초과됐습니다.") from exc
        finally:
            if proc.poll() is None:
                stop_group(proc)


def _raw_changes(data):
    """Consume only complete NUL-delimited records, including both sides of renames."""
    fields = iter(data.split(b"\0")[:-1])
    for header in fields:
        parts = header.split()
        if len(parts) != 5 or not parts[0].startswith(b":"):
            raise WorkflowError("Git 변경 목록 형식을 해석할 수 없습니다.")
        old = next(fields, None)
        kind = parts[-1][:1]
        new = next(fields, None) if kind in (b"R", b"C") else old
        if old is None or new is None:
            break
        paths = {os.fsdecode(old), os.fsdecode(new)}
        regular = (kind != b"U" and all(mode in (b"000000", b"100644", b"100755")
                                        for mode in (parts[0][1:], parts[1])))
        yield kind, paths, regular


def _patch_text(raw, partial):
    # Never send a partial line. A cutoff could otherwise separate a value from its marker.
    if partial:
        raw = raw[:raw.rfind(b"\n") + 1]
    if b"\x00" in raw or b"\nBinary files " in raw or b"PRIVATE KEY-----" in raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = []
    for line in text.splitlines(keepends=True):
        # A PEM body may appear without its BEGIN marker in a small hunk.
        if (_SECRET_LINE.search(line) or _SECRET_TOKEN.search(line)
                or re.fullmatch(r"[+ -]\s*[A-Za-z0-9+/]{40,}={0,2}\s*", line)):
            prefix = line[:1] if line[:1] in ("+", "-", " ") else ""
            lines.append(prefix + "[REDACTED]\n")
        else:
            lines.append(line)
    return "".join(lines)


def change_context(project, base=None, *, excluded_paths=()):
    """Return repo-relative paths and diff against a local commit plus untracked path names."""
    deadline = time.monotonic() + 30
    root_data, capped = _read_git(Path(project).expanduser().resolve(),
                                  ["rev-parse", "--show-toplevel"], 16384, deadline)
    if capped:
        raise WorkflowError("Git 프로젝트 경로가 너무 깁니다.")
    root = Path(os.fsdecode(root_data.removesuffix(b"\n")))
    config, capped = _read_git(root, ["config", "--null", "--name-only", "--list"], _METADATA_BYTES, deadline)
    if capped:
        raise WorkflowError("Git 필터 설정 전체를 확인할 수 없습니다.")
    drivers = {key.rsplit(b".", 1)[0].removeprefix(b"filter.") for key in config.split(b"\0")
               if key.startswith(b"filter.") and key.endswith((b".clean", b".process"))}
    options = _filter_overrides(drivers)

    ref = "HEAD" if base is None else base
    if not isinstance(ref, str) or not ref or ref.startswith("-") or "\0" in ref:
        raise WorkflowError("기준으로 사용할 로컬 커밋 또는 브랜치를 지정하세요.")
    oid, _ = _read_git(root, ["rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}"],
                       128, deadline, allow_absent=True)
    sha = oid.strip().decode("ascii")
    if not sha and base is not None:
        raise WorkflowError("요청한 기준 커밋을 로컬 저장소에서 찾을 수 없습니다.")

    common = [*options, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules=dirty"]
    staged = ["--cached", *([sha] if sha else [])]
    layers = [("base -> index", staged), ("index -> worktree", [])]
    excluded = {Path(entry) for entry in excluded_paths}
    changed, blocked, opaque = set(), set(), set()
    tracked = []
    renames = []
    incomplete_tracked = False
    for _, revision in layers:
        raw, cut = _read_git(root, [*common, "--raw", "-z", "--find-renames", "-l100", *revision, "--"],
                             _METADATA_BYTES, deadline)
        incomplete_tracked |= cut
        paths_in_layer = set()
        added = set()
        sensitive_delete = False
        for kind, names, regular in _raw_changes(raw):
            paths_in_layer.update(names)
            if kind == b"D" and any(_sensitive(Path(name), excluded) for name in names):
                sensitive_delete = True
            elif kind == b"A":
                added.update(names)
            elif kind in (b"R", b"C"):
                renames.append(names)
            if not regular:
                opaque.update(names)
        # A heavily edited move can look like an unrelated deletion and addition.
        if sensitive_delete:
            opaque.update(added)
        changed.update(paths_in_layer)
        tracked.append(paths_in_layer)
    raw, cut = _read_git(root, [*options, "ls-files", "--others", "--exclude-standard", "-z"],
                         _METADATA_BYTES, deadline)
    untracked = {os.fsdecode(name) for name in raw.split(b"\0")[:-1] if name}
    changed.update(untracked)
    truncated = incomplete_tracked or cut
    blocked.update(name for name in changed if _sensitive(Path(name), excluded))
    for names in renames:
        if blocked.intersection(names):
            blocked.update(names)
    # An unstaged rename of an already renamed path can taint its earlier name too.
    for names in reversed(renames):
        if blocked.intersection(names):
            blocked.update(names)

    visible = sorted(changed - blocked)
    # Prioritize tracked changes when many generated untracked files compete for space.
    visible.sort(key=lambda path: path in untracked)
    paths = [name for name in visible if len(os.fsencode(name)) <= PATH_BYTES
             and not any(ord(ch) < 32 or 0x7f <= ord(ch) <= 0x9f or 0xd800 <= ord(ch) <= 0xdfff for ch in name)][:PATH_LIMIT]
    truncated |= len(paths) < len(visible)
    sections = []
    remaining = DIFF_BYTES
    skipped = set()
    for (label, revision), names in zip(layers, tracked):
        for path in paths:
            if path not in names:
                continue
            heading = "\n[" + label + "]\n"
            cap = min(FILE_DIFF_BYTES, remaining - len(heading))
            if incomplete_tracked or path in opaque or cap < 256:
                skipped.add(path)
                continue
            raw, cut = _read_git(root, [*common, "--patch", "--unified=2", "--no-renames",
                                        *revision, "--", path], cap, deadline)
            patch = _patch_text(raw, cut)
            if cut or patch is None:
                skipped.add(path)
            if patch:
                encoded = patch.encode()
                if len(encoded) > cap:
                    patch = encoded[:cap].decode(errors="ignore").rsplit("\n", 1)[0]
                    skipped.add(path)
                sections.append(heading + patch)
                remaining -= len(heading) + len(patch.encode())
    tracked_visible = (set.union(*tracked) - blocked) - set(paths)
    skipped.update(tracked_visible)
    return {"base": sha or None, "paths": paths, "untracked_paths": [name for name in paths if name in untracked],
            "diff": "".join(sections), "truncated": truncated or bool(skipped), "omitted_sensitive": len(blocked),
            "omitted_diff_files": len(skipped)}
