UPDATE jobs
SET job_status = 'active'
WHERE id IN (
    SELECT j.id
    FROM jobs j
    JOIN candidate_profiles cp ON cp.job_id = j.id
    WHERE j.job_status = 'no_candidates'
    GROUP BY j.id
);
