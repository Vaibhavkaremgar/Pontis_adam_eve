# Phase 1 Operational & Risk Mitigation Guide

**Date:** 2026-07-16  
**Audience:** DevOps, Database Administrators, Release Engineers  
**Status:** Ready for Production Operations

---

## Pre-Deployment Checklist

### Database Backup & Safety
- [ ] Full database backup created and verified restorable
- [ ] Read replicas synchronized and healthy (if applicable)
- [ ] Database monitoring alerts active
- [ ] Connection pooling stable (check PgBouncer/pgpool)
- [ ] No long-running transactions blocking

### Application Readiness
- [ ] ORM code using `CompanyEntity` reviewed
- [ ] No breaking changes in dependent services
- [ ] API endpoints for agencies reviewed
- [ ] Feature flags ready (if needed for gradual rollout)
- [ ] Staging environment migration tested

### Alembic Migration Verification
- [ ] Migration file syntax validated
- [ ] Revision ID unique and follows naming convention
- [ ] Parent migration ID (`down_revision`) correct
- [ ] `alembic current` shows migration chain integrity
- [ ] Test run on staging database completed successfully

### Documentation Review
- [ ] Engineering team reviewed Phase 1 scope
- [ ] Rollback procedures documented and tested
- [ ] Monitoring queries prepared
- [ ] On-call engineer briefed on changes

### Monitoring & Observability
- [ ] Query performance baseline established
- [ ] Slow query logging enabled
- [ ] Index creation monitoring configured
- [ ] Disk space monitoring active (should not be issue for small table)

---

## Deployment Steps

### 1. Pre-Deployment Validation

```bash
# Verify Alembic health
cd backend
alembic current          # Should show current migration
alembic history -v       # Should show clean chain
alembic branches         # Should have no branches (single head)
```

**Expected Output:**
```
Current revision for <connection string>:
<previous_migration_id> (head)

...

5 migrations listed, no branches found.
```

### 2. Dry-Run on Staging

```bash
# Backup staging database first
pg_dump -U postgres staging_db > staging_backup_$(date +%s).sql

# Run migration on staging
cd backend
alembic upgrade phase_1_create_agencies

# Verify table exists
psql -U postgres -d staging_db -c "\d agencies"
```

**Expected Output:**
```
                          Table "public.agencies"
      Column      |           Type           | Collation | Nullable | Default
------------------+----------------------------+-----------+----------+---------
 id               | uuid                     |           | not null |
 name             | character varying(255)   |           | not null |
 website          | character varying(500)   |           | not null |
 description      | text                     |           | not null | ''::text
 industry         | character varying(255)   |           | not null | ''::text
 ats_provider     | character varying(64)    |           | not null | ''::text
 ats_connected    | boolean                  |           | not null | false
 user_id          | uuid                     |           | not null |
 created_at       | timestamp with time zone |           | not null | now()
Indexes:
    "agencies_pkey" PRIMARY KEY, btree (id)
    "ix_agencies_user_id" btree (user_id)
    "uq_companies_user_name" UNIQUE CONSTRAINT, btree (user_id, name)
```

### 3. Rollback Test on Staging

```bash
# Test rollback procedure
cd backend
alembic downgrade -1

# Verify table is gone
psql -U postgres -d staging_db -c "SELECT to_regclass('public.agencies');"

# Verify result is NULL (table doesn't exist)
# Then roll forward again
alembic upgrade phase_1_create_agencies
```

### 4. Production Deployment

```bash
# Set maintenance window notification (optional)
# Notify team of maintenance start

# Create production backup
pg_dump -U postgres production_db > production_backup_$(date +%s).sql

# SSH to production database server or application server with DB access
cd backend

# Run production migration
alembic upgrade phase_1_create_agencies

# Monitor migration execution (should complete in <1 second)
```

### 5. Post-Deployment Verification

```bash
# Verify table structure
psql -U postgres -d production_db << 'EOF'
\d agencies
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_name = 'agencies'
ORDER BY ordinal_position;
EOF

# Verify constraints
psql -U postgres -d production_db << 'EOF'
SELECT constraint_name, constraint_type 
FROM information_schema.table_constraints 
WHERE table_name = 'agencies';
EOF

# Verify indexes
psql -U postgres -d production_db << 'EOF'
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'agencies';
EOF

# Verify FK relationship
psql -U postgres -d production_db << 'EOF'
SELECT constraint_name, table_name, column_name, referenced_table_name 
FROM information_schema.referential_constraints 
WHERE constraint_name LIKE '%agencies%';
EOF

# Count rows (should be 0)
psql -U postgres -d production_db -c "SELECT COUNT(*) FROM agencies;"

# Check table size
psql -U postgres -d production_db -c "SELECT pg_size_pretty(pg_total_relation_size('agencies'));"
```

### 6. Application Verification

```bash
# Check if application can connect and query
# Test basic ORM operations
python -c "
from app.models.entities import CompanyEntity
from app.db import SessionLocal

session = SessionLocal()
result = session.query(CompanyEntity).first()
print(f'Query successful: {result}')
session.close()
"

# Check application logs for errors
tail -100 /var/log/application/app.log | grep -i error
```

### 7. Monitoring Post-Deployment

```bash
# Monitor query performance (first 24 hours)
watch -n 5 "psql -U postgres -d production_db << 'EOF'
SELECT 
  query,
  calls,
  total_time,
  mean_time
FROM pg_stat_statements 
WHERE query LIKE '%agencies%'
ORDER BY total_time DESC;
EOF"

# Monitor index usage
watch -n 30 "psql -U postgres -d production_db << 'EOF'
SELECT 
  indexname,
  idx_scan as scans,
  idx_tup_read as tuples_read,
  idx_tup_fetch as tuples_returned
FROM pg_stat_user_indexes
WHERE relname = 'agencies';
EOF"
```

---

## Rollback Procedure

### Immediate Rollback (If Issues Detected)

```bash
# Stop accepting new requests (optional, depends on availability requirements)

# Rollback migration
cd backend
alembic downgrade -1

# Monitor for errors
tail -50 /var/log/application/app.log

# Application continues to work (queries will just return no agencies)
# Notify team of rollback
```

### Why Rollback is Safe
- ✅ No cascading dependencies (table was new)
- ✅ No data loss (empty table anyway)
- ✅ Foreign key still references valid users table
- ✅ Application has no hardcoded dependency on table existing

### When NOT to Rollback
- ❌ If agencies have been created in production (would lose data)
- ❌ If application code depends on table existing

---

## Risk Mitigation Strategies

### Risk 1: Foreign Key Constraint Fails

**Mitigation:**
```bash
# Pre-flight check: Verify users table exists
psql -U postgres -d production_db -c "SELECT COUNT(*) FROM users;"

# Pre-flight check: Verify no NULL users exist (shouldn't have any)
psql -U postgres -d production_db -c "SELECT COUNT(*) FROM users WHERE id IS NULL;"
```

**Likelihood:** 🟢 Very Low (users table is prerequisite)  
**Impact:** Migration fails cleanly, no table created  
**Detection:** Clear error message from Alembic

### Risk 2: Migration Timeout

**Mitigation:**
```bash
# Monitor migration process
# CREATE TABLE is O(1) operation - should take <1 second
# If running >30 seconds, something is very wrong - check for locks

# Check for blocking transactions
psql -U postgres -d production_db << 'EOF'
SELECT 
  pid,
  usename,
  application_name,
  query,
  query_start
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;
EOF
```

**Likelihood:** 🟢 Virtually Impossible (no data to move)  
**Impact:** Migration hangs, but can be killed without data loss  
**Detection:** Monitor transaction logs

### Risk 3: Unique Constraint Conflict

**Mitigation:**
```bash
# Pre-flight check: Query should return 0 (no existing agencies)
psql -U postgres -d production_db -c "SELECT COUNT(*) FROM agencies;" 2>/dev/null || echo "Table doesn't exist yet (expected)"

# After migration: Manually test unique constraint
psql -U postgres -d production_db << 'EOF'
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
VALUES (
  gen_random_uuid(),
  'Test Agency',
  'https://example.com',
  'Test',
  'Technology',
  (SELECT id FROM users LIMIT 1),
  CURRENT_TIMESTAMP
);

-- Duplicate should fail
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
VALUES (
  gen_random_uuid(),
  'Test Agency',
  'https://example.com',
  'Test',
  'Technology',
  (SELECT id FROM users LIMIT 1),
  CURRENT_TIMESTAMP
);
EOF
```

**Likelihood:** 🟢 No (table starts empty)  
**Impact:** N/A  
**Detection:** Constraint works as designed

### Risk 4: Application Sees New Table Before Code Deployed

**Mitigation:**
```bash
# Option 1: Deploy application first
# This ensures ORM can handle empty agencies table

# Option 2: Use feature flag
# In application.py:
#   if FEATURE_AGENCIES_ENABLED:
#       query_agencies()

# Option 3: Gradual rollout
# Use canary deployment: 10% → 50% → 100%
```

**Likelihood:** 🟡 Low (coordination issue)  
**Impact:** Application tries to use table before code ready, errors in logs  
**Detection:** Monitor application error logs for `RelationNotFoundError` or similar

---

## Monitoring & Health Checks

### Real-Time Monitoring Command

```bash
#!/bin/bash
# monitor_agencies.sh - Run during/after deployment

while true; do
  clear
  echo "=== Agencies Table Health Check ==="
  echo "Time: $(date)"
  echo ""
  
  psql -U postgres -d production_db << 'EOF'
  SELECT 
    'Table Exists' as check,
    CASE WHEN to_regclass('public.agencies') IS NOT NULL THEN '✓ YES' ELSE '✗ NO' END as status;
  
  SELECT 
    'Row Count' as check,
    COUNT(*) as status
  FROM agencies;
  
  SELECT 
    'Indexes' as check,
    COUNT(*) as status
  FROM pg_indexes 
  WHERE tablename = 'agencies';
  
  SELECT 
    'Constraints' as check,
    COUNT(*) as status
  FROM information_schema.table_constraints 
  WHERE table_name = 'agencies';
EOF
  
  sleep 5
done
```

### Post-Deployment Checklist

```bash
# Run these queries 1, 5, 15, 60 minutes post-deployment

## 1-Minute Check
psql -U postgres -d production_db << 'EOF'
-- Verify table structure
\d agencies
EOF

## 5-Minute Check  
psql -U postgres -d production_db << 'EOF'
-- Check for any slow queries on agencies table
SELECT query, mean_time 
FROM pg_stat_statements 
WHERE query LIKE '%agencies%'
ORDER BY mean_time DESC;
EOF

## 15-Minute Check
psql -U postgres -d production_db << 'EOF'
-- Verify no unexpected locks
SELECT * FROM pg_locks WHERE relation = 'agencies'::regclass;
EOF

## 60-Minute Check
psql -U postgres -d production_db << 'EOF'
-- Review application error logs for any FK or constraint errors
-- (This must be done via application log aggregation system)
EOF
```

---

## Troubleshooting Guide

### Symptom: Migration fails with "Table already exists"

**Cause:** Idempotent migration already ran or manual table creation happened  
**Fix:** Check current migration status
```bash
cd backend
alembic current
alembic history | grep phase_1_create_agencies
```

**Action:** If migration already applied, move on. If not, investigate why table exists.

### Symptom: Application gets "ERROR: relation 'agencies' does not exist"

**Cause:** Migration not run or was rolled back  
**Fix:**
```bash
# Verify migration status
cd backend
alembic current

# If not at phase_1_create_agencies, run upgrade
alembic upgrade phase_1_create_agencies

# Or if at wrong revision, check history
alembic history -v
```

### Symptom: Foreign key constraint violation when inserting

**Cause:** `user_id` value doesn't exist in users table  
**Fix:**
```bash
# Verify users exist
psql -U postgres -d production_db -c "SELECT COUNT(*) FROM users;"

# Use valid user_id from existing users
psql -U postgres -d production_db << 'EOF'
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
VALUES (
  gen_random_uuid(),
  'Test Agency',
  'https://example.com',
  'Test',
  'Technology',
  (SELECT id FROM users LIMIT 1),  -- Use real user
  CURRENT_TIMESTAMP
);
EOF
```

### Symptom: Unique constraint violation: "(user_id, name)"

**Cause:** User already has agency with this name  
**Fix:**
```bash
# Check existing agencies
psql -U postgres -d production_db << 'EOF'
SELECT user_id, name, COUNT(*) 
FROM agencies 
GROUP BY user_id, name 
HAVING COUNT(*) > 1;
EOF

# Use different name or different user
```

### Symptom: Slow query after deployment

**Cause:** Index not being used or data skew  
**Fix:**
```bash
# Check explain plan
psql -U postgres -d production_db << 'EOF'
EXPLAIN ANALYZE
SELECT * FROM agencies WHERE user_id = (SELECT id FROM users LIMIT 1);
EOF

# Should show index scan on ix_agencies_user_id
# If not, check index statistics
ANALYZE agencies;
```

---

## Performance Baseline

### Expected Performance (Before Significant Data)

| Operation | Latency | Notes |
|-----------|---------|-------|
| Table creation | <1 sec | One-time during migration |
| Insert 1 row | 1-5 ms | Normal INSERT operation |
| Query by user_id | <1 ms | Uses index (ix_agencies_user_id) |
| Query by id | <1 ms | Uses PK index |
| Update | 2-10 ms | Depends on index maintenance |
| Delete | 2-10 ms | Depends on index maintenance |
| Unique constraint check | <1 ms | Very fast operation |

### Monitoring Query Performance

```sql
-- After 24 hours, check actual query times
SELECT 
  query,
  calls,
  total_time,
  mean_time,
  max_time
FROM pg_stat_statements 
WHERE query LIKE '%agencies%'
ORDER BY total_time DESC;
```

---

## Runbook Summary

### Quick Reference Card

```
DEPLOYMENT COMMAND
$ cd backend && alembic upgrade phase_1_create_agencies

VERIFICATION
$ psql -d production_db -c "\d agencies"
$ psql -d production_db -c "SELECT COUNT(*) FROM agencies;"

ROLLBACK COMMAND
$ cd backend && alembic downgrade -1

MONITORING
$ watch -n 5 "psql -d production_db -c \"SELECT COUNT(*) FROM agencies;\""

TROUBLESHOOTING
1. Check migration status: alembic current
2. Check application logs for errors
3. Verify users table exists: SELECT COUNT(*) FROM users;
4. Test insert with valid user_id
```

---

## Post-Deployment Success Criteria

✅ All checks passed:
- [ ] Table exists with correct structure
- [ ] All 9 columns present
- [ ] Constraints enforced (PK, FK, UNIQUE)
- [ ] Indexes created
- [ ] Foreign key relationship works
- [ ] Application queries execute without errors
- [ ] No slow queries detected
- [ ] Zero deadlocks or lock conflicts
- [ ] Database disk space healthy
- [ ] Monitoring shows no anomalies

✅ Rollback test passed:
- [ ] Downgrade command executes successfully
- [ ] Table drops cleanly
- [ ] Upgrade can run again without issues

✅ Operational readiness:
- [ ] Team notified of deployment
- [ ] Runbook available to on-call engineer
- [ ] Monitoring dashboards created
- [ ] Alerts configured
- [ ] Documentation updated

---

## Contact & Escalation

| Role | Contact | Responsibility |
|------|---------|-----------------|
| Database Admin | [DBA] | Backup, restore, migration execution |
| DevOps Engineer | [DevOps] | Monitoring, alerts, application deployment |
| Senior Engineer | [Engineer] | Troubleshooting, rollback decisions |
| Product Manager | [PM] | Feature flag coordination if needed |

**Escalation Path:**  
1. DBA/DevOps assesses issue
2. If needs code changes → Senior Engineer
3. If needs rollback → Database Admin + DevOps
4. If needs communication → Product Manager

---

**Status:** ✅ Ready for Production Operations  
**Last Reviewed:** 2026-07-16  
**Next Review:** After successful deployment
