from app.schemas.candidate import CandidateResult
fields = list(CandidateResult.model_fields.keys())
print('CandidateResult fields:', fields)
print('Has role:', 'role' in fields)
print('Has company:', 'company' in fields)
