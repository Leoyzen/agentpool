# Decisions

## 2026-04-24 Session Start
- Use RFC Option 1: providers call create_child_session() and emit events themselves
- Team.run()/TeamRun.run() non-streaming paths are OUT OF SCOPE
- EventManager._forward_to_parent() is OUT OF SCOPE
- agentpool_commands/pool.py CLI depth behavior is OUT OF SCOPE
- No DelegationProvider base class
- No SessionManager.create_top_level_session()
- Session ID format change accepted (opaque strings)
- Team/TeamRun must pop session_id and depth from **kwargs before forwarding
