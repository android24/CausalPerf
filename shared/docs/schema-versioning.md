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

## Bundle release

`shared/schema-bundle.lock.json` is the released `causalperf-contracts@0.6.0`
contract set. It enumerates every shared, Agent and Bench JSON Schema by path,
`$id`, document version and raw-byte SHA-256, and seals that list with a
canonical bundle digest. Adding, removing or editing any Schema requires an
intentional bundle-version update.

The executable registry in
`shared/reference/causalperf_reference/schema_registry.py` verifies exact file
membership and hashes. Its migration registry contains only released semantic
transitions; most document types remain v1. Isolation Policy, Run and Report
are v2 because the Windows backend adds drive-letter absolute paths and a new
backend identity.
Their v1 schemas are retained under `causalperf-bench/schemas/archive/`, and a
pure contiguous migration changes only `schema_version` and reseals the record;
their original raw-byte hashes remain locked as v1 entries alongside v2 under
the same schema IDs. The registry therefore keys uniqueness by schema ID and
document version. The migration information-loss list is empty. Tests prove
same-version migration is a pure idempotent copy and that unknown future
versions and downgrades fail closed.

Task Reproduction Package is also v2. Its migration binds each legacy artifact
to the conservative role implied by v1: task-definition material to
`DEVELOPMENT`, experiment evidence to `CALIBRATION`, and replay/leakage review
to `QUALIFICATION`. It does not invent fresh qualification measurements, so a
migrated v1 package cannot satisfy `QUALIFIED` completeness unless distinct
qualification artifacts are subsequently recorded.
