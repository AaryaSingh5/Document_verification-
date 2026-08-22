-- Migration: 001_add_document_verifications.sql
-- Description: Create document verifications table and enums for Suraksha Setu tourist safety system.

-- 1. Create Enums if they do not already exist
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_type_enum') THEN
        CREATE TYPE document_type_enum AS ENUM (
            'PASSPORT',
            'DRIVING_LICENCE',
            'VOTER_ID',
            'OTHER_GOVERNMENT_ID'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'verification_status_enum') THEN
        CREATE TYPE verification_status_enum AS ENUM (
            'PENDING',
            'EXTRACTED',
            'VERIFIED',
            'PENDING_REVIEW',
            'REUPLOAD_REQUIRED',
            'REJECTED'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'field_status_enum') THEN
        CREATE TYPE field_status_enum AS ENUM (
            'FOUND',
            'NEEDS_REVIEW',
            'NOT_FOUND'
        );
    END IF;
END $$;

-- 2. Create document_verifications table
CREATE TABLE IF NOT EXISTS document_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tourist_id UUID,
    document_type document_type_enum NOT NULL,
    status verification_status_enum NOT NULL DEFAULT 'PENDING',
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0.000,
    storage_key VARCHAR(255) NOT NULL,
    document_number_masked VARCHAR(64) NOT NULL,
    extracted_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_data JSONB,
    rejection_reasons TEXT[] DEFAULT ARRAY[]::TEXT[],
    ocr_provider VARCHAR(50) NOT NULL DEFAULT 'mock',
    is_mock BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified_at TIMESTAMPTZ,

    -- Constraint ensuring confidence stays between 0 and 1
    CONSTRAINT chk_confidence_range CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

-- 3. Create Indexes for query performance
CREATE INDEX IF NOT EXISTS idx_doc_verifications_tourist_id
    ON document_verifications(tourist_id);

CREATE INDEX IF NOT EXISTS idx_doc_verifications_status
    ON document_verifications(status);

CREATE INDEX IF NOT EXISTS idx_doc_verifications_created_at
    ON document_verifications(created_at DESC);

-- 4. Automatic updated_at timestamp trigger
CREATE OR REPLACE FUNCTION update_doc_verifications_modtime()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_doc_verifications_updated_at ON document_verifications;
CREATE TRIGGER trg_doc_verifications_updated_at
    BEFORE UPDATE ON document_verifications
    FOR EACH ROW
    EXECUTE FUNCTION update_doc_verifications_modtime();

-- Down Migration / Rollback Instructions:
-- DROP TABLE IF EXISTS document_verifications;
-- DROP FUNCTION IF EXISTS update_doc_verifications_modtime;
-- DROP TYPE IF EXISTS field_status_enum;
-- DROP TYPE IF EXISTS verification_status_enum;
-- DROP TYPE IF EXISTS document_type_enum;
