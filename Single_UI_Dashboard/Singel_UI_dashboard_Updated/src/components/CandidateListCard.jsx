const REC_COLORS = {
  'Strong Hire': { bg: 'var(--success-bg)', color: 'var(--success)' },
  'Hire':        { bg: 'var(--primary-bg)', color: 'var(--primary)' },
  'Hold':        { bg: 'var(--warning-bg)', color: 'var(--warning)' },
  'Reject':      { bg: 'var(--danger-bg)', color: 'var(--danger)' },
};

function scoreColor(sc) {
  return 'var(--primary)';
}

export default function CandidateListCard({ interview, selected, onClick, loading }) {
  if (loading) return (
    <div style={s.card}>
      <div className="skeleton" style={{ width: 44, height: 44, borderRadius: '50%', flexShrink: 0 }} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="skeleton" style={{ width: '70%', height: 13 }} />
        <div className="skeleton" style={{ width: '50%', height: 11 }} />
      </div>
    </div>
  );

  const { candidate, overallScore, recommendation, date } = interview;
  const rec = REC_COLORS[recommendation] ?? { bg: '#f1f5f9', color: '#475569' };

  return (
    <div
      style={{ ...s.card, ...(selected ? s.cardActive : {}) }}
      onClick={onClick}
    >
      <div style={{ ...s.avatar, background: selected ? 'linear-gradient(135deg,var(--primary),var(--primary-secondary))' : 'linear-gradient(135deg,#94a3b8,#64748b)' }}>
        {candidate.name[0]}
      </div>
      <div style={s.info}>
        <div style={s.name}>{candidate.name}</div>
        <div style={s.role}>{candidate.role}</div>
        <div style={s.meta}>{date}</div>
      </div>
      <div style={s.right}>
        <div style={{ ...s.score, color: scoreColor(overallScore) }}>{overallScore}</div>
        <span style={{ ...s.badge, background: rec.bg, color: rec.color }}>{recommendation}</span>
      </div>
    </div>
  );
}

const s = {
  card: {
    display: 'flex', alignItems: 'center', gap: 12,
    background: 'var(--surface)', borderRadius: 20, padding: '16px',
    boxShadow: 'var(--shadow)', cursor: 'pointer',
    border: '1px solid var(--border)', transition: 'border-color .15s, box-shadow .15s, transform .15s',
  },
  cardActive: {
    border: '1px solid var(--primary)',
    boxShadow: '0 0 0 3px rgba(91, 61, 245, .12)',
  },
  avatar: {
    width: 44, height: 44, borderRadius: '50%', flexShrink: 0,
    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 18, fontWeight: 700,
  },
  info: { flex: 1, minWidth: 0 },
  name: { fontSize: 16, fontWeight: 700, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  role: { fontSize: 14, color: 'var(--text-muted)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  meta: { fontSize: 14, color: 'var(--text-light)', marginTop: 2 },
  right: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, flexShrink: 0 },
  score: { fontSize: 18, fontWeight: 700, lineHeight: 1 },
  badge: { fontSize: 12, fontWeight: 600, borderRadius: 999, padding: '4px 10px', whiteSpace: 'nowrap' },
};
