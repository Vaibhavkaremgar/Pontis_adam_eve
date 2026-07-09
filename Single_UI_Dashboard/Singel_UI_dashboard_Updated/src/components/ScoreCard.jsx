const REC_COLORS = {
  'Strong Hire': { bg: 'var(--success-bg)', color: 'var(--success)', border: '#bbf7d0' },
  'Hire':        { bg: 'var(--primary-bg)', color: 'var(--primary)', border: '#ddd6fe' },
  'Hold':        { bg: 'var(--warning-bg)', color: 'var(--warning)', border: '#fde68a' },
  'Reject':      { bg: 'var(--danger-bg)', color: 'var(--danger)', border: '#fecaca' },
};

function scoreColor(s) {
  return 'var(--primary)';
}

export default function ScoreCard({ score, recommendation, loading, compact = false }) {
  if (loading) return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }}>
      <div className="skeleton" style={{ width: 120, height: 15, marginBottom: 12 }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="skeleton" style={{ width: 96, height: 40 }} />
        <div className="skeleton" style={{ width: 86, height: 26, borderRadius: 20 }} />
      </div>
    </div>
  );

  const rec   = REC_COLORS[recommendation] ?? { bg: '#f3f4f6', color: 'var(--text-muted)', border: '#e5e7eb' };
  const color = scoreColor(score);

  return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null), borderLeft: `4px solid ${color}` }} className="fade-in">
      <div style={{ ...s.label, ...(compact ? s.labelCompact : null) }}>Overall Score</div>
      <div style={s.row}>
        <div style={{ ...s.score, ...(compact ? s.scoreCompact : null), color }}>{score}<span style={{ ...s.denom, ...(compact ? s.denomCompact : null) }}> / 10</span></div>
        <span style={{ ...s.badge, ...(compact ? s.badgeCompact : null), background: rec.bg, color: rec.color, border: `1px solid ${rec.border}` }}>
          {recommendation}
        </span>
      </div>
    </div>
  );
}

const s = {
  card: {
    background: 'var(--surface)', borderRadius: 20,
    boxShadow: 'var(--shadow)', border: '1px solid var(--border)', padding: '24px',
  },
  cardCompact: { padding: '16px' },
  label: { fontSize: 18, color: 'var(--text-muted)', fontWeight: 500, marginBottom: 12 },
  labelCompact: { fontSize: 13, marginBottom: 8 },
  row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  score: { fontSize: 44, fontWeight: 700, lineHeight: 1 },
  scoreCompact: { fontSize: 34 },
  denom: { fontSize: 18, fontWeight: 500, color: 'var(--text-muted)' },
  denomCompact: { fontSize: 14 },
  badge: { borderRadius: 999, padding: '8px 18px', fontSize: 18, fontWeight: 600, whiteSpace: 'nowrap' },
  badgeCompact: { padding: '6px 12px', fontSize: 13 },
};
