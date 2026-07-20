from app.models.entities import CandidateProfileEntity
cols = [c.key for c in CandidateProfileEntity.__table__.columns]
print('Columns:', cols)
print('Has role:', hasattr(CandidateProfileEntity, 'role'))
print('Has company:', hasattr(CandidateProfileEntity, 'company'))
print('Has current_role:', hasattr(CandidateProfileEntity, 'current_role'))
print('Has current_company:', hasattr(CandidateProfileEntity, 'current_company'))
print('Has current_title:', hasattr(CandidateProfileEntity, 'current_title'))
print('Has agency:', hasattr(CandidateProfileEntity, 'agency'))
