"""Installed local-agent-workflow core required; unavailable core must not block work."""
try:
    from agent_workflow.plugin_hook import main
    main("claude")
except (Exception, SystemExit):
    pass
