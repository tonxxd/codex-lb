## ADDED Requirements

### Requirement: Abliteration accepts compatible historical transcripts

When forwarding Responses input to `api.abliteration.ai`, the proxy SHALL omit
provider-owned reasoning history and translate completed custom tool calls and
their results to equivalent function-call history, preserving call identifiers,
tool names, input text, and result text. For a text-only model, historical tool
result images SHALL be replaced by an explicit text marker. User messages and
current user images MUST remain unchanged. Other source payloads MUST remain
unchanged.

#### Scenario: GPT history continues on Abliteration
- **WHEN** GPT history contains encrypted reasoning, completed custom tool calls and tool-result images
- **AND** the next request targets an Abliteration text-only model
- **THEN** the source receives supported function-call history and readable results
- **AND** visible conversation text remains present
