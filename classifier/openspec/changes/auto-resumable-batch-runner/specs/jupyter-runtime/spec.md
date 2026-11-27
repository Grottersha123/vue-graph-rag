# Spec Delta: Automatic batch execution

## ADDED Requirements

### Requirement: Notebook can process missing batches automatically

The notebook SHALL expose `AUTO_START_BATCH_INDEX` and `AUTO_BATCH_COUNT`. It SHALL process selected batches sequentially through image description and classification stages, skip only completed batches with all required artifacts, and update each manifest.

#### Scenario: Process a range of batches

- **WHEN** `BATCH_SIZE=30`, `MAX_CASES=1000`, `AUTO_START_BATCH_INDEX=1` and `AUTO_BATCH_COUNT=None`
- **THEN** the runner processes ranges beginning with `[30:60]` and stops after `[990:1000]`

#### Scenario: Resume after interruption

- **WHEN** a batch manifest is `parsed` or `failed`, or a completed manifest is missing any required artifact
- **THEN** the runner processes that batch again instead of reporting it as completed

#### Scenario: Skip completed work

- **WHEN** a batch manifest has `status=completed` and all cases, image, result and error artifacts exist
- **THEN** the runner skips model calls for that batch and proceeds to the next one

#### Scenario: Fatal endpoint failure

- **WHEN** the image or text endpoint raises a fatal API error
- **THEN** the current manifest becomes `failed` with the failed stage and the automatic loop stops
