# Issues and Blockers

## Risk 1: shutdown race between event signaling and task cancellation
- Current `BaseServer.stop()` cancels task immediately without setting `_shutdown_event`
- Mitigation: Ensure ACP server stop path explicitly signals shutdown first

## Risk 2: initialize guard leaks into non-agent connection paths
- `Connection` is shared plumbing between client and agent sides
- Mitigation: Keep guard state in `AgentSideConnection` only

## Risk 3: hidden legacy dependency on `--transport websocket`
- `src/agentpool_cli/ui.py` uses `--transport websocket --ws-port ...`
- Mitigation: Migrate Toad helper to new transport in same change

## Risk 4: dependency scope mismatch for `starlette`
- ACP server should not rely on unrelated optional extra
- Mitigation: Promote `starlette` to main dependency set
