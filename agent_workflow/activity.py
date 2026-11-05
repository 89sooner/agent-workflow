"""Activity labels derived only from explicit tool-start metadata."""
TOOLS = {
    'reading': {'read', 'grep', 'glob', 'list', 'ls'},
    'writing': {'write', 'edit', 'apply_patch', 'multiedit'},
    'command': {'bash', 'shell', 'exec_command', 'run_command'},
}


def activity(state, fresh, event, tool):
    if not fresh:
        return 'ended' if state == 'ended' else 'unknown'
    if state != 'running':
        return state
    if event in {'PreToolUse', 'tool.execute.before'}:
        tool = (tool or '').lower()
        for kind, names in TOOLS.items():
            if tool in names:
                return kind
    return 'working'
