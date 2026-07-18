## ADDED Requirements

### Requirement: ALLOW_MODEL_REQUESTS global gate with enforcement

The system SHALL set `ALLOW_MODEL_REQUESTS = False` as a module-level constant in `tests/conftest.py` to prevent all real model API calls during test execution by default. The gate MUST be enforced at the httpx transport level via an autouse pytest fixture that installs a blocking `MockTransport` handler. This gate MUST block any `BaseAgent` or model client from making outbound HTTP requests to model providers, not merely serve as a documentation convention.

#### Scenario: Default test run blocks real model calls

- **WHEN** a test runs without the `allow_model_requests` fixture and without `@pytest.mark.vcr`
- **THEN** any attempt to make a real model API HTTP call SHALL raise a `RuntimeError` with a message explaining how to use VCR or the `allow_model_requests` fixture

#### Scenario: allow_model_requests fixture enables real calls

- **WHEN** a test uses the `allow_model_requests` fixture
- **THEN** real model API calls SHALL be permitted for the duration of that test

#### Scenario: VCR tests do not need allow_model_requests

- **WHEN** a test is marked with `@pytest.mark.vcr` and has a matching cassette
- **THEN** the VCR middleware SHALL intercept HTTP requests and replay from cassette without requiring `allow_model_requests`

### Requirement: VCR cassette infrastructure

The system SHALL provide VCR cassette recording and replay infrastructure using `pytest-recording` and `vcrpy`. Cassettes SHALL be stored at `tests/cassettes/<module_path>/<test_function>.yaml` with auto-naming. The `@pytest.mark.vcr` marker SHALL enable VCR replay for marked tests.

#### Scenario: VCR test replays from cassette

- **WHEN** a test is marked with `@pytest.mark.vcr` and a cassette exists at the expected path
- **THEN** HTTP requests to model APIs SHALL be intercepted and matched against cassette interactions
- **AND** the test SHALL receive recorded responses without making real network calls

#### Scenario: VCR test without cassette fails

- **WHEN** a test is marked with `@pytest.mark.vcr` and no cassette exists
- **THEN** the test SHALL attempt to record (if `--record-mode` permits) or fail with a clear error

#### Scenario: Recording a new cassette

- **WHEN** a developer runs `pytest tests/vcr/test_acp_flow.py --record-mode=once`
- **THEN** real API calls SHALL be made and recorded to `tests/cassettes/vcr/test_acp_flow/<test_function>.yaml`
- **AND** the cassette SHALL be committed to git for CI replay

### Requirement: Cassette sanitization

The system SHALL sanitize all cassettes to prevent credential leakage. The `vcr_config` fixture SHALL filter sensitive headers (`authorization`, `x-api-key`, `cookie`, `set-cookie`), decode compressed response bodies, and normalize unicode characters in JSON bodies.

#### Scenario: Authorization header is scrubbed

- **WHEN** a cassette is recorded that contains an `authorization` header
- **THEN** the header value SHALL be replaced with `REDACTED` in the committed cassette file

#### Scenario: Compressed response bodies are decoded

- **WHEN** a cassette records a response with `content-encoding: gzip`
- **THEN** the cassette SHALL store the decoded body, not the compressed bytes

### Requirement: Strict cassette usage enforcement

The system SHALL enforce strict cassette usage in CI via `--strict-vcr-cassette-usage`. After each VCR test, the system SHALL verify that ALL cassette interactions were played. A cassette with unused interactions SHALL cause the test to fail.

#### Scenario: All cassette interactions are played

- **WHEN** a VCR test completes and all interactions in the cassette were matched and played
- **THEN** the test SHALL pass

#### Scenario: Cassette has unused interactions

- **WHEN** a VCR test completes but some cassette interactions were not played
- **THEN** the test SHALL fail with a message listing the unused interactions

### Requirement: Cassette hygiene checking

The system SHALL provide a `check_cassettes.py` script that verifies every `.yaml` cassette file in `tests/cassettes/` has a corresponding test function. The script SHALL be run as a CI step.

#### Scenario: Orphaned cassette detected

- **WHEN** a cassette file exists at `tests/cassettes/vcr/test_foo/test_bar.yaml` but no test function `test_bar` exists in `tests/vcr/test_foo.py`
- **THEN** `check_cassettes.py` SHALL report the orphaned cassette and exit with non-zero status

#### Scenario: All cassettes have corresponding tests

- **WHEN** every cassette file has a matching test function
- **THEN** `check_cassettes.py` SHALL exit with zero status

### Requirement: VCR test without cassette detection

The system SHALL provide a `check_vcr_tests.py` script that finds `@pytest.mark.vcr` test functions WITHOUT corresponding cassette files. This is the inverse of `check_cassettes.py` and catches tests that were marked VCR but never had cassettes recorded. The script SHALL be run as a CI step.

#### Scenario: VCR test without cassette detected

- **WHEN** a test function `test_bar` in `tests/vcr/test_foo.py` is marked with `@pytest.mark.vcr` but no cassette file exists at `tests/cassettes/vcr/test_foo/test_bar.yaml`
- **THEN** `check_vcr_tests.py` SHALL report the missing cassette and exit with non-zero status

#### Scenario: All VCR tests have cassettes

- **WHEN** every `@pytest.mark.vcr` test function has a matching cassette file
- **THEN** `check_vcr_tests.py` SHALL exit with zero status

### Requirement: Assertion libraries for VCR tests

The system SHALL provide `dirty-equals` and `inline-snapshot` as dev dependencies for VCR test assertions. `dirty-equals` enables partial/fuzzy matching of API response fields (`IsStr`, `IsDatetime`, `IsNow`, `IsPartialDict`). `inline-snapshot` enables deterministic snapshot assertions for protocol-defined shapes (message structures, event schemas). VCR tests SHOULD use these libraries instead of exact equality checks to tolerate non-deterministic fields (timestamps, UUIDs).

#### Scenario: VCR test uses dirty-equals for partial matching

- **WHEN** a VCR test asserts on a response field that contains a timestamp
- **THEN** the test SHALL use `IsDatetime` or `IsNow` from `dirty-equals` instead of comparing against an exact value

#### Scenario: VCR test uses inline-snapshot for schema pinning

- **WHEN** a VCR test asserts on a complete response structure (e.g., a chat completion response schema)
- **THEN** the test MAY use `inline-snapshot` with `snapshot(...)` to pin the exact shape, updated via `--inline-snapshot=update`
