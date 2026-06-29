## MODIFIED Requirements

### Requirement: Version negotiation SHALL be integrated into DispatchAgent at runtime

`VersionNegotiator` SHALL be called by `DispatchAgent.initialize()` to determine the protocol version. The negotiator's `negotiate()` method SHALL receive the `protocolVersion` from the client's `initialize` request and return the negotiated version. DispatchAgent SHALL use this result to select the delegate agent.

#### Scenario: DispatchAgent calls VersionNegotiator

- **WHEN** DispatchAgent receives an `initialize` request
- **THEN** it SHALL call `VersionNegotiator.negotiate(params.protocol_version)` to determine the version
- **AND** use the result to select between v1 and v2 delegate agents

#### Scenario: VersionNegotiator error propagates to client

- **WHEN** `VersionNegotiator.negotiate()` raises `RequestError` for an unsupported version
- **THEN** DispatchAgent SHALL propagate the error to the client as a JSON-RPC error response
