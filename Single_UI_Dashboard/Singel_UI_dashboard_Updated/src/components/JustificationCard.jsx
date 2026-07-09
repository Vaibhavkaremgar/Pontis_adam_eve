export default function JustificationCard({ text, loading, compact = false }) {
  if (loading) return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }}>
      <div className="skeleton" style={{ width: 160, height: 15, marginBottom: 10 }} />
      <div className="skeleton" style={{ height: 13, marginBottom: 6 }} />
      <div className="skeleton" style={{ height: 13, marginBottom: 6, width: '92%' }} />
      <div className="skeleton" style={{ height: 13, width: '78%' }} />
    </div>
  );

  return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }} className="fade-in">
      <h3 style={{ ...s.title, ...(compact ? s.titleCompact : null) }}>Score Justification</h3>
      <p style={{ ...s.text, ...(compact ? s.textCompact : null) }}>{text}</p>
    </div>
  );
}

const s = {
  card: {
    background: 'var(--surface)', borderRadius: 20,
    boxShadow: 'var(--shadow)', border: '1px solid var(--border)', padding: '24px',
  },
  cardCompact: { padding: '16px' },
  title: { fontSize: 28, fontWeight: 700, color: 'var(--text)', marginBottom: 16 },
  titleCompact: { fontSize: 17, marginBottom: 10 },
  text: { fontSize: 16, color: 'var(--text-body)', lineHeight: 1.75 },
  textCompact: { fontSize: 15, lineHeight: 1.68 },
};
