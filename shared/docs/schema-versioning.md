# Schema versioning and canonical digests

## Compatibility policy

Every persisted document carries an integer `schema_version`. Schema files are
immutable after release; a semantic or structural change creates a new version
and migration function. Editing historical artifacts in place is forbidden.

| Change | Version action | Reader behavior |
|---|---|---|
| description or example only | no schema bump | accept |
| add optional field without changed meaning | new schema version | old reader may ignore only after explicit compatibility declaration |
| add required field, enum value, invariant or changed meaning | new schema version and migration | reject unknown version |
| remove or rename field | new schema version and migration | reject without migration |

Version support is allowlisted. A reader must never silently treat an unknown
future version as the current version.

## Canonical JSON digest

For records whose schema contains `content_sha256`:

1. remove only the top-level `content_sha256` field;
2. serialize UTF-8 JSON with keys sorted, no insignificant whitespace, Unicode
   preserved, and separators `,` and `:`;
3. preserve JSON array order;
4. hash the resulting bytes with SHA-256 in lowercase hexadecimal.

No timestamps, unknown fields, nulls, or nested digest fields are implicitly
removed. Floating-point producers must emit finite JSON numbers; NaN and
infinity are invalid. File artifacts use their raw-byte SHA-256 rather than a
record digest.

## Migration requirements

Each migration is a pure function `vN -> vN+1` with:

- a fixture for the old version;
- expected canonical output for the new version;
- an idempotence test on already migrated input;
- an explicit list of information loss, normally empty;
- regenerated `content_sha256`;
- no access to private evaluator data, network, clock, or device state.

Until a type has a second released version, its migration registry is empty and
unknown versions fail closed. This is preferable to inventing unused migration
code before a real compatibility change exists.
