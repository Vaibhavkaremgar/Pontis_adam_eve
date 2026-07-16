-- Phase 1 PostgreSQL Migration
-- Create agencies table with Phase 1 columns
-- Production-safe, idempotent migration
-- Revision: phase_1_create_agencies
-- Date: 2026-07-16

-- ==============================================================================
-- UPGRADE: Create agencies table
-- ==============================================================================

CREATE TABLE IF NOT EXISTS agencies (
    id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    website VARCHAR(500) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    industry VARCHAR(255) NOT NULL DEFAULT '',
    ats_provider VARCHAR(64) NOT NULL DEFAULT '',
    ats_connected BOOLEAN NOT NULL DEFAULT false,
    user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT uq_companies_user_name UNIQUE (user_id, name)
);

-- Create index on user_id for efficient lookups
CREATE INDEX IF NOT EXISTS ix_agencies_user_id ON agencies (user_id);

-- ==============================================================================
-- Post-Migration Verification Queries
-- ==============================================================================

-- Verify table structure
-- SELECT column_name, data_type, is_nullable, column_default 
-- FROM information_schema.columns 
-- WHERE table_name = 'agencies'
-- ORDER BY ordinal_position;

-- Verify constraints
-- SELECT constraint_name, constraint_type 
-- FROM information_schema.table_constraints 
-- WHERE table_name = 'agencies';

-- Verify foreign key relationship
-- SELECT constraint_name, table_name, column_name, referenced_table_name, referenced_column_name
-- FROM information_schema.referential_constraints 
-- WHERE table_name = 'agencies';

-- ==============================================================================
-- DOWNGRADE: Drop agencies table (if needed)
-- ==============================================================================
-- DROP INDEX IF EXISTS ix_agencies_user_id;
-- DROP TABLE IF EXISTS agencies;
