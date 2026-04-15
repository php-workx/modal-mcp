# Modal Fixture Replay Format

Modal replay fixtures are scrubbed JSON documents checked into
`tests/fixtures/modal/`.

Required fields:

- `source_modal_version`: Modal package version used during capture.
- `captured_at`: UTC timestamp for the capture.
- `rpc`: Modal RPC or public SDK operation name.
- `request_metadata`: deterministic request metadata after redaction.
- `protobuf_wire_bytes_b64`: base64-encoded protobuf wire bytes where
  applicable, or an empty string for public SDK calls without raw protobuf.
- `response_payload`: deterministic redacted response payload used by replay
  tests.

Secrets, service-user ids, bearer values, and environment-specific paths must be
redacted before committing. Fixture replay must not require Modal credentials.
