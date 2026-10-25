# Schema Drift Process Gate (PR-14)

**ARCHITECT §15.4, ROADMAP §5.2.14**

This document describes the process gate for maintaining schema consistency between QdrantPayload and Postgres `documents` table. Schema drift is prevented through automated contract tests and a strict change process.

## Overview

The system uses a dual-channel architecture where documents are stored in both:
- PostgreSQL (lexical search, document metadata)
- Qdrant (vector search, payload with chunk metadata)

Schema drift between these sources can cause search inconsistencies, data loss, or indexing failures. The contract tests enforce consistency automatically.

## Field Mapping

### QdrantPayload ↔ Document Columns

| QdrantPayload field | Document column     | Notes                          |
|---------------------|---------------------|--------------------------------|
| doc_id              | id                  | Primary key                    |
| tenant_id           | tenant_id           | Tenant identifier              |
| language            | language            | Document language              |
| tags                | tags                | ARRAY(Text) → list[str]        |
| attributes          | attributes          | JSONB → dict[str, Any]         |
| model_name          | embedding_model     | Embedding model name           |
| model_rev           | embedding_rev       | Model revision                 |
| created_at          | created_at          | Timestamp with UTC timezone    |
| content_hash        | content_hash        | Content fingerprint            |
| chunk_idx           | -                   | Qdrant-side chunk index (PR-13)|
| section_type        | -                   | Qdrant-side section type (PR-13)|

### Excluded Fields (Not in Payload)

These Document columns are intentionally excluded from QdrantPayload:
- `external_ref`, `title`, `content` (not stored in vector index)
- `updated_at`, `deleted_at` (internal state, not searchable)
- `tsv` (PostgreSQL text search vector)

## Change Process

### When Modifying QdrantPayload

1. **Update field mapping** in `tests/contract/test_schema_drift.py`:
   - Add/remove field in `PAYLOAD_TO_DOCUMENTS` if it maps to a Document column
   - Add/remove field in `PAYLOAD_ONLY` if it's Qdrant-side only
   - Update `test_qdrant_payload_matches_documents_schema()`

2. **Update documentation** in `app/search/qdrant_payload.py`:
   - Update the field mapping table
   - Update process description if needed

3. **Run contract tests**:
   ```bash
   pytest tests/contract/ -v  # Fast tests (no Docker)
   pytest tests/contract/ -m slow  # Roundtrip test (requires Docker)
   ```

4. **Ensure all tests pass** — if any fail, the change is blocked until consistency is restored

### When Modifying Document Table (via Alembic)

1. **Update field mapping** in `tests/contract/test_schema_drift.py`:
   - Add/remove column in `PAYLOAD_TO_DOCUMENTS` if it maps to a QdrantPayload field
   - Add/remove column in `DOCUMENTS_EXCLUDED` if it should remain payload-only
   - Update `test_documents_fields_covered_in_payload()`

2. **Update documentation** in `app/search/qdrant_payload.py`:
   - Update the field mapping table
   - Update process description if needed

3. **Run contract tests** (same as above)

4. **Ensure backward compatibility** for existing QdrantPayload instances

### When Modifying Both Simultaneously

1. Update both mapping locations
2. Update documentation
3. Run contract tests
4. Fix any inconsistencies until all tests pass

## Contract Tests

The test suite in `tests/contract/test_schema_drift.py` provides:

1. **Static tests** (no infrastructure):
   - `test_qdrant_payload_matches_documents_schema()` - Ensures every payload field is classified
   - `test_documents_fields_covered_in_payload()` - Ensures every document column is classified
   - `test_qdrant_payload_extra_forbid()` - Validates extra field rejection
   - `test_chunk_fields_in_payload()` - Validates PR-13 chunk metadata fields

2. **Roundtrip test** (`@pytest.mark.slow`):
   - `test_qdrant_point_payload_roundtrip()` - End-to-end verification:
     - Index document via `IndexingService`
     - Process via `ReconcilerWorker` (asymmetric path)
     - Retrieve from Qdrant
     - Compare payload with original document

## CI Integration

Contract tests are integrated into the production gate:

- **Fast tests** run always: `pytest tests/contract/ -v`
- **Slow tests** run when infrastructure is available: `pytest tests/contract/ -m slow`

Release is blocked if any contract test fails.

## Troubleshooting

### Common Failures

1. **"Unclassified field" error**:
   - New field added to QdrantPayload or Document without updating mapping constants
   - Solution: Update constants in `tests/contract/test_schema_drift.py`

2. **"Excluded column in payload" error**:
   - Document column that should remain excluded is present in QdrantPayload
   - Solution: Update `DOCUMENTS_EXCLUDED` constants

3. **"Type mismatch" error**:
   - Pydantic field type doesn't match SQLAlchemy column type
   - Solution: Update field definitions or adjust type mapping

4. **Roundtrip test failures**:
   - Payload retrieved from Qdrant doesn't match expected from Document
   - Solution: Check field mapping, serialization, and chunk metadata handling

### Debug Commands

```bash
# Run only static contract tests
pytest tests/contract/ -v

# Run only roundtrip test
pytest tests/contract/ -m slow -v

# Run specific test
pytest tests/contract/test_schema_drift.py::test_qdrant_payload_matches_documents_schema -v

# Run with verbose output
pytest tests/contract/ -v --tb=long
```

## Related Components

- **ARCHITECT.md**: §3.2 (QdrantPayload), §15.4 (Schema Drift Mitigation)
- **ROADMAP.md**: §5.2.14 (PR-14 Schema Drift Contract Tests)
- **app/search/qdrant_payload.py**: Canonical payload model
- **tests/contract/test_schema_drift.py**: Contract test suite
- **scripts/run_dod_check_prod.sh**: CI gate integration

## History

- **PR-14**: Initial implementation of schema drift contract tests
- **P-13**: Added chunk metadata fields (chunk_idx, section_type) to QdrantPayload
- **P-04**: Established QdrantPayload as canonical model