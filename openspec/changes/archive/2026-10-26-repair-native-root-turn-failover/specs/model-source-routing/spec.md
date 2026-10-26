## ADDED Requirements

### Requirement: Abliteration vision replay preserves historical tool images

For a vision-capable Abliteration model, the proxy SHALL retain historical
tool-result images in labeled user messages following their contiguous tool
outputs, using text references in the original outputs.

#### Scenario: A conversation with earlier tool images switches to a vision model
- **GIVEN** paired historical tool results contain text and image content
- **WHEN** the conversation is forwarded to a vision-capable Abliteration model
- **THEN** the original text and image data SHALL be retained
- **AND** image data SHALL appear in labeled message content rather than inside tool outputs
- **AND** contiguous parallel tool results SHALL remain together
- **AND** current user images and other providers SHALL retain their existing behavior
