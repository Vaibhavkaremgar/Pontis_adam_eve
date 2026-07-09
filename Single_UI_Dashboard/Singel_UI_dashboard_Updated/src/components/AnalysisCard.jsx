export default function AnalysisCard({ text, loading, compact = false, scrollable = false, numbered = false }) {
  if (loading) return (
    <div style={{ ...s.wrap, ...s.loadingWrap(compact) }}>
      <div className="skeleton" style={{ width: compact ? 150 : 160, height: compact ? 16 : 20, marginBottom: compact ? 12 : 14 }} />
      {[1, 2, 3].map(i => (
        <div key={i} className="skeleton" style={{ height: 14, marginBottom: 8, width: i === 3 ? '70%' : '100%' }} />
      ))}
    </div>
  );

  const narrative = (text ?? '')
    .split(/\n+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' ');

  const chunks = narrative
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
    .map((part) => part.trim())
    .filter(Boolean);

  return (
    <div style={{ ...s.wrap, ...(scrollable ? s.scrollWrap : null) }} className="fade-in">
      <h3 style={{ ...s.title, ...(compact ? s.titleCompact : null) }}>Interview Analysis</h3>
      <div style={{ ...s.card, ...(compact ? s.cardCompact : null), ...(scrollable ? s.scrollCard : null) }}>
        {numbered ? (
          <div style={s.numberedList}>
            {chunks.length ? chunks.map((item, index) => {
              const { lead, body } = splitLeadAndBody(item);
              return (
              <div key={`${index}-${item.slice(0, 12)}`} style={s.itemRow}>
                <div style={s.numBadge}>{index + 1}</div>
                <div style={s.itemBody}>
                  <div style={s.itemLead}>{lead}</div>
                  {body ? <p style={{ ...s.para, ...(compact ? s.paraCompact : null) }}>{body}</p> : null}
                </div>
              </div>
            )}) : <p style={{ ...s.para, ...(compact ? s.paraCompact : null) }}>{narrative}</p>}
          </div>
        ) : (
          <p style={{ ...s.para, ...(compact ? s.paraCompact : null) }}>{narrative}</p>
        )}
      </div>
    </div>
  );
}

function splitLeadAndBody(text) {
  const parts = text.split(/(?<=[.!?])\s+/).map((part) => part.trim()).filter(Boolean);
  if (!parts.length) return { lead: 'Observation', body: text };
  if (parts.length === 1) {
    return { lead: parts[0], body: parts[0] };
  }
  return { lead: parts[0], body: parts.slice(1).join(' ') };
}

const s = {
  wrap: {},
  loadingWrap: (compact) => ({ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, gap: 0 }),
  title: { fontSize: 28, fontWeight: 700, color: 'var(--text)', marginBottom: 16 },
  titleCompact: { fontSize: 17, marginBottom: 10 },
  card: {
    background: 'var(--surface)',
    borderRadius: 20,
    boxShadow: 'var(--shadow)',
    border: '1px solid var(--border)',
    padding: '22px',
  },
  cardCompact: {
    padding: '16px',
    minHeight: 0,
  },
  scrollWrap: {
    display: 'flex',
    flexDirection: 'column',
    minHeight: 0,
    height: '100%',
  },
  scrollCard: {
    flex: 1,
    minHeight: 0,
    overflow: 'auto',
  },
  numberedList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  itemRow: {
    display: 'grid',
    gridTemplateColumns: 'auto minmax(0, 1fr)',
    gap: 14,
    alignItems: 'start',
  },
  numBadge: {
    width: 32,
    height: 32,
    borderRadius: 12,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--primary-bg)',
    color: 'var(--primary)',
    fontSize: 14,
    fontWeight: 700,
  },
  itemBody: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    minWidth: 0,
  },
  itemLead: {
    fontSize: 15,
    fontWeight: 700,
    color: 'var(--text)',
  },
  para: { fontSize: 16, color: 'var(--text-body)', lineHeight: 1.75, margin: 0 },
  paraCompact: { fontSize: 15, lineHeight: 1.7 },
};
