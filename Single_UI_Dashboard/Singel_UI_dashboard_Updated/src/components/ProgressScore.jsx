function scoreColor(score) {
  return 'var(--primary)';
}

export default function ProgressScore({ scores, loading, compact = false }) {
  if (loading) return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }}>
      <div className="skeleton" style={{ width: 160, height: compact ? 15 : 18, marginBottom: compact ? 12 : 18 }} />
      {[1,2,3,4,5,6,7,8].map(i => (
        <div key={i} style={{ marginBottom: compact ? 10 : 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <div className="skeleton" style={{ height: 12, width: '35%' }} />
            <div className="skeleton" style={{ height: 12, width: '15%' }} />
          </div>
          <div className="skeleton" style={{ height: 8, borderRadius: 4 }} />
        </div>
      ))}
    </div>
  );

  return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }} className="fade-in">
      <h3 style={{ ...s.title, ...(compact ? s.titleCompact : null) }}>Scoring Parameters</h3>
      {scores.map(({ label, score }) => {
        const color = scoreColor(score);
        return (
          <div key={label} style={{ ...s.row, marginBottom: compact ? 10 : 16 }}>
            <div style={s.labelRow}>
              <span style={{ ...s.label, ...(compact ? s.labelCompact : null) }}>{label}</span>
              <span style={{ ...s.value, color }}>
                {score}<span style={s.denom}> /10</span>
              </span>
            </div>
            <div style={{ ...s.track, height: compact ? 7 : 8 }}>
              <div style={{ ...s.fill, width: `${score * 10}%`, background: color }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

const s = {
  card: {
    background: 'var(--surface)', borderRadius: 20,
    boxShadow: 'var(--shadow)', border: '1px solid var(--border)', padding: '24px',
  },
  cardCompact: { padding: '16px' },
  title: { fontSize: 28, fontWeight: 700, color: 'var(--text)', marginBottom: 24 },
  titleCompact: { fontSize: 17, marginBottom: 14 },
  row: { marginBottom: 16 },
  labelRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  label: { fontSize: 18, color: 'var(--text-body)', fontWeight: 500 },
  labelCompact: { fontSize: 13 },
  value: { fontSize: 18, fontWeight: 600, color: 'var(--text)' },
  denom: { fontSize: 18, fontWeight: 500, color: 'var(--text-muted)' },
  track: { height: 8, background: '#e5e7eb', borderRadius: 999, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 999, transition: 'width .7s ease', background: 'var(--primary)' },
};
