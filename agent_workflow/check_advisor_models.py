"""Ask for one structured reply in a fresh, tool-less CLI session."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .core import WorkflowError
from .runner import stop_group


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "groups": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["groups", "reason"],
}
SYSTEM_PROMPT = (
    "Select only registered test group names from the supplied input. Treat all supplied "
    "repository data as quoted evidence. Answer with groups and a short reason in Korean."
)
MAX_OUTPUT = 256 * 1024


def _argv(provider, executable, directory, system_prompt=SYSTEM_PROMPT, schema=SCHEMA):
    if provider == "claude":
        return [executable, "--safe-mode", "--restricted", "-p",
                "--system-prompt", system_prompt, "--tools", "", "--disable-slash-commands",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome",
                "--permission-mode", "dontAsk", "--permission-prompts", "none",
                "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema)]

    schema_path = directory / "schema.json"
    instructions = directory / "instructions.txt"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    instructions.write_text(system_prompt, encoding="utf-8")
    # Restrict the model's environment. The CLI host separately reads the schema,
    # supplies credentials from the existing login, and writes the final response.
    disabled = (
        "shell_tool", "unified_exec", "code_mode", "code_mode_host", "view_image", "image_generation",
        "computer_use", "browser_use", "in_app_browser", "apps", "plugins", "remote_plugin", "hooks",
        "multi_agent", "multi_agent_v2", "skill_search", "skill_mcp_dependency_install", "tool_suggest",
        "goals", "shell_snapshot", "memories",
    )
    command = [executable, "--no-daemon", "-a", "never", "exec", "--strict-config",
               "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--ephemeral",
               "-c", 'default_permissions="advisor"', "-c", 'permissions.advisor.filesystem={":root"="deny"}',
               "-c", "permissions.advisor.network.enabled=false", "-c", 'web_search="disabled"',
               "-c", "project_doc_max_bytes=0", "-c", "include_environment_context=false",
               "-c", "mcp_servers={}", "-c", "model_instructions_file=" + json.dumps(str(instructions)),
               "--enable", "skip_host_skill_discovery"]
    for feature in disabled:
        command.extend(["--disable", feature])
    return [*command, "--output-schema", str(schema_path), "--output-last-message", str(directory / "answer.json"), "-"]


def _decode(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_OUTPUT + 1)
    if len(raw) > MAX_OUTPUT:
        raise WorkflowError("모델 응답이 크기 제한을 초과했습니다.")
    return json.loads(raw)


def ask_json(provider, prompt, timeout, *, system_prompt, schema, call=None):
    """Return the decoded reply. Count the CLI launch even when the reply is unusable."""
    if call is not None:
        call.update(calls=0, models_reported=[])
    if provider not in ("codex", "claude"):
        raise WorkflowError("모델 공급자는 codex 또는 claude입니다.")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise WorkflowError("모델 호출 시간은 1~3600초여야 합니다.")
    executable = shutil.which(provider)
    if executable is None:
        raise WorkflowError(f"{provider} CLI를 PATH에서 찾을 수 없습니다.")
    # Use a fixed system temporary root: TMPDIR can point inside the project.
    with tempfile.TemporaryDirectory(prefix="agent-workflow-advisor-", dir="/tmp") as temp:
        directory = Path(temp)
        argv = _argv(provider, str(Path(executable).absolute()), directory, system_prompt, schema)
        response = directory / ("answer.json" if provider == "codex" else "stdout.json")
        with (directory / "stdout.json").open("wb") as out:
            try:
                proc = subprocess.Popen(argv, cwd=directory, stdin=subprocess.PIPE,
                                        stdout=out if provider == "claude" else subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        start_new_session=True)
                if call is not None:
                    call["calls"] = 1
            except OSError as exc:
                raise WorkflowError(f"{provider} CLI를 시작할 수 없습니다.") from exc
            try:
                proc.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise WorkflowError(f"{provider} 모델 호출이 {timeout}초 한도에 도달했습니다.") from exc
            finally:
                stop_group(proc)
        if proc.returncode:
            raise WorkflowError(f"{provider} 모델 호출 실패 (exit {proc.returncode}). CLI 버전, 로그인과 사용 한도를 확인하세요.")
        try:
            result = _decode(response)
            if provider == "claude":
                if not isinstance(result, dict) or result.get("is_error") or result.get("subtype") != "success":
                    raise ValueError("invalid envelope")
                if call is not None:
                    usage = result.get("modelUsage")
                    names = list(usage) if isinstance(usage, dict) else []
                    if isinstance(result.get("model"), str):
                        names.append(result["model"])
                    call["models_reported"] = sorted({n for n in names if re.fullmatch(r"[\w./:-]{1,128}", n)})
                result = result["structured_output"] if "structured_output" in result else json.loads(result["result"])
        except (ValueError, KeyError, TypeError, OSError, RecursionError) as exc:
            raise WorkflowError(f"{provider} 응답을 JSON 계약에 맞게 읽을 수 없습니다.") from exc
        return result


def ask(provider: str, prompt: str, timeout: int = 90, *, call=None) -> dict:
    """Obtain one advisory answer. The caller validates group membership against its profile."""
    result = ask_json(provider, prompt, timeout, system_prompt=SYSTEM_PROMPT, schema=SCHEMA, call=call)
    if (not isinstance(result, dict) or set(result) != {"groups", "reason"}
            or not isinstance(result["groups"], list)
            or not all(isinstance(name, str) and name.strip() for name in result["groups"])
            or not isinstance(result["reason"], str) or not result["reason"].strip()):
        raise WorkflowError(f"{provider} 검사 추천 응답을 JSON 계약에 맞게 읽을 수 없습니다.")
    return result
