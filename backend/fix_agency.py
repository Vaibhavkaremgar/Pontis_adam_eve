f = open('app/services/candidate_service.py', 'rb')
c = f.read()
f.close()

old = (
    b'        agency_for_matching = str(getattr(job_for_matching, "company_id", "") or "")\n'
    b'        return list(match_internal_candidates_for_job(db=db, job_id=job_id, agency_id=agency_for_matching)["candidates"])'
)

new = (
    b'        agency_for_matching = str(getattr(job_for_matching, "company_id", "") or "").strip()\n'
    b'        if not agency_for_matching:\n'
    b'            logger.warning(\n'
    b'                "candidate_refresh_skipped job_id=%s reason=missing_agency_id",\n'
    b'                job_id,\n'
    b'            )\n'
    b'            return []\n'
    b'        return list(match_internal_candidates_for_job(db=db, job_id=job_id, agency_id=agency_for_matching)["candidates"])'
)

count = c.count(old)
print(f'Pattern found: {count} time(s)')
assert count == 1, f'Expected exactly 1 match, got {count}'

c2 = c.replace(old, new, 1)
f = open('app/services/candidate_service.py', 'wb')
f.write(c2)
f.close()
print('Fix applied successfully.')
