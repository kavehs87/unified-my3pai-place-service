-- loadtest/cleanup.sql
-- Remove all test data created by loadtest scripts.
-- All write tests use source values prefixed with 'loadtest' or exactly 'batch-import'.
-- This cleanup contract ensures real data is never touched.

-- Soft-delete first (matches app semantics — DELETE sets is_active=false)
UPDATE entities SET is_active = false WHERE source LIKE 'loadtest%' OR source = 'batch-import';

-- Hard-delete to reclaim space and keep staging clean for next run.
-- CASCADE removes associated media, classifications, routes.
DELETE FROM entities WHERE source LIKE 'loadtest%' OR source = 'batch-import';

-- Verify count returns to baseline
SELECT count(*) AS active_entities FROM entities WHERE is_active = true;
SELECT count(*) AS leftover_test_entities FROM entities WHERE source LIKE 'loadtest%' OR source = 'batch-import';
