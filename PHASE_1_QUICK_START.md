# ⚡ Phase 1 Migration - Quick Start (2-Minute Read)

**Status:** ✅ Ready for Production  
**Risk Level:** 🟢 LOW  
**Execution Time:** <1 second

---

## What's Happening?

Creating the `agencies` table with 3 new fields:
- 🆕 **website** - Company website URL
- 🆕 **description** - Agency description  
- 🆕 **industry** - Industry classification

**Why?** To support the unified database while preserving the current user-based authorization model.

---

## The Table

```sql
CREATE TABLE agencies (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    website VARCHAR(500) NOT NULL,        ⭐ NEW
    description TEXT NOT NULL DEFAULT '',  ⭐ NEW
    industry VARCHAR(255) NOT NULL DEFAULT '', ⭐ NEW
    ats_provider VARCHAR(64) NOT NULL DEFAULT '',
    ats_connected BOOLEAN NOT NULL DEFAULT false,
    user_id UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
```

---

## Deploy in 3 Steps

### 1️⃣ Backup
```bash
pg_dump -U postgres production_db > backup_$(date +%s).sql
```

### 2️⃣ Deploy
```bash
cd backend
alembic upgrade phase_1_create_agencies
```

### 3️⃣ Verify
```bash
psql -d production_db -c "\d agencies"
```

**Total time:** 15-20 minutes (mostly testing)

---

## What's NOT Changing

✅ **Kept unchanged:**
- `user_id` (authorization model)
- `ats_provider` (will move in Phase 2)
- `ats_connected` (will move in Phase 2)
- All existing tables
- Authorization logic

❌ **Not included:**
- Multi-user access control (Phase 3)
- ATS integration table (Phase 2)
- Roles or membership tables (Phase 3)

---

## Risk Assessment

| Area | Status |
|------|--------|
| Data Loss | ✅ NONE |
| Downtime | ✅ ZERO |
| Performance | ✅ SAFE |
| Rollback | ✅ EASY |
| Application Breaking | ✅ NO |

**Overall:** 🟢 **LOW RISK**

---

## Quick Reference

### Already Prepared ✅
- ORM model in `entities.py` - Ready to use
- Database schema - Clean structure
- Documentation - Comprehensive guides

### Need to Do 📋
1. Review documentation
2. Test on staging
3. Schedule deployment
4. Run 3 commands above
5. Verify table

### Can Rollback ↩️
```bash
cd backend
alembic downgrade -1
```

---

## Documentation Map

| Need | File |
|------|------|
| 5-min overview | PHASE_1_EXECUTIVE_SUMMARY.md |
| Full details | PHASE_1_MIGRATION.md |
| Schema deep-dive | PHASE_1_SCHEMA_SPECIFICATION.md |
| How to deploy | PHASE_1_OPERATIONAL_GUIDE.md |
| Troubleshooting | PHASE_1_OPERATIONAL_GUIDE.md |
| Index/navigation | PHASE_1_INDEX.md |

---

## Success = This Works

```bash
# After deployment:
psql -d production_db << 'EOF'
SELECT COUNT(*) FROM agencies;  -- Returns: 0
\d agencies                      -- Shows correct structure
EOF
```

---

## Questions?

- **Technical?** → PHASE_1_SCHEMA_SPECIFICATION.md
- **Deploying?** → PHASE_1_OPERATIONAL_GUIDE.md  
- **Risk?** → PHASE_1_EXECUTIVE_SUMMARY.md
- **Lost?** → PHASE_1_INDEX.md

---

## Next Step

👉 **Read:** [PHASE_1_EXECUTIVE_SUMMARY.md](PHASE_1_EXECUTIVE_SUMMARY.md)

Then follow [PHASE_1_OPERATIONAL_GUIDE.md](PHASE_1_OPERATIONAL_GUIDE.md) for deployment.

---

**Status:** ✅ Ready to deploy  
**Time to deploy:** 15-20 minutes  
**Risk:** 🟢 Low  
**Documentation:** Complete  

**You're all set! 🚀**
