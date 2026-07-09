import { Calendar, Hash } from 'lucide-react';

export default function InterviewHeader({ id, date, duration, loading }) {
  if (loading) return (
    <div style={s.wrap}>
      <div>
        <div className="skeleton" style={{ width: 240, height: 28, marginBottom: 8 }} />
        <div className="skeleton" style={{ width: 200, height: 14 }} />
      </div>
      <div style={{ display: 'flex', gap: 10 }}>
        {[1,2,3].map(i => <div key={i} className="skeleton" style={{ width: 110, height: 32, borderRadius: 20 }} />)}
      </div>
    </div>
  );

  const formatted = date
    ? new Date(date).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
    : '—';

  return (
    <div style={s.wrap} className="fade-in">
      <div>
        <h1 style={s.title}>Interview Dashboard</h1>
        <p style={s.sub}>AI-powered candidate evaluation report</p>
      </div>
      <div style={s.badges}>
        <Badge icon={Hash}     text={id} />
        <Badge icon={Calendar} text={formatted} />
      </div>
    </div>
  );
}

function Badge({ icon: Icon, text }) {
  return (
    <div style={s.badge}>
      <Icon size={22} color="var(--primary)" />
      <span style={s.badgeText}>{text}</span>
    </div>
  );
}

const s = {
  wrap: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    flexWrap: 'wrap', gap: 24, marginBottom: 24,
  },
  title: { fontSize: 40, fontWeight: 700, color: 'var(--text)' },
  sub: { fontSize: 18, fontWeight: 400, color: 'var(--text-muted)', marginTop: 6 },
  badges: { display: 'flex', gap: 12, flexWrap: 'wrap' },
  badge: {
    display: 'flex', alignItems: 'center', gap: 10,
    background: 'var(--primary-bg)', borderRadius: 999, padding: '8px 18px',
  },
  badgeText: { fontSize: 14, fontWeight: 600, color: 'var(--primary)' },
};
