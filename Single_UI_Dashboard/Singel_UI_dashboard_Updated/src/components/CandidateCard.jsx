import { MapPin, Briefcase } from 'lucide-react';

const REC_COLORS = {
  'Strong Hire': { bg: 'var(--success-bg)', color: 'var(--success)', border: '#bbf7d0' },
  'Hire':        { bg: 'var(--primary-bg)', color: 'var(--primary)', border: '#ddd6fe' },
  'Hold':        { bg: 'var(--warning-bg)', color: 'var(--warning)', border: '#fde68a' },
  'Reject':      { bg: 'var(--danger-bg)', color: 'var(--danger)', border: '#fecaca' },
};

function scoreColor(s) {
  return 'var(--primary)';
}

export default function CandidateCard({ candidate, overallScore, recommendation, loading }) {
  if (loading || !candidate) return (
    <div style={s.card}>
      <div style={{ display: 'flex', gap: 20, alignItems: 'center', flex: 1 }}>
        <div className="skeleton" style={{ width: 72, height: 72, borderRadius: '50%', flexShrink: 0 }} />
        <div style={{ flex: 1, display: 'flex', gap: 32, flexWrap: 'wrap' }}>
          {[180, 120, 140, 130].map((w, i) => (
            <div key={i} className="skeleton" style={{ width: w, height: 16 }} />
          ))}
        </div>
      </div>
      <div className="skeleton" style={{ width: 140, height: 80, borderRadius: 12, flexShrink: 0 }} />
    </div>
  );

  const { name, role, experience, location, availability } = candidate;
  const rec = REC_COLORS[recommendation] ?? { bg: '#f3f4f6', color: 'var(--text-muted)', border: '#e5e7eb' };
  const sc  = scoreColor(overallScore);

  return (
    <div style={s.card} className="fade-in">
      <div style={s.left}>
        <div style={s.avatar}>{name?.[0] ?? 'C'}</div>
        <div style={s.info}>
          <div style={s.name}>{name}</div>
          <div style={s.role}>{role}</div>
          <div style={s.pills}>
            <Pill icon={Briefcase} text={experience} />
            <Pill icon={MapPin}    text={location} />
          </div>
        </div>
      </div>
      <div style={s.vDivider} />
      <div style={s.scoreBox}>
        <div style={s.scoreLabel}>Overall Score</div>
        <div style={{ ...s.scoreNum, color: sc }}>{overallScore}<span style={s.scoreDenom}> / 10</span></div>
        <span style={{ ...s.recBadge, background: rec.bg, color: rec.color, border: `1px solid ${rec.border}` }}>
          {recommendation}
        </span>
      </div>
    </div>
  );
}

function Pill({ icon: Icon, text }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <Icon size={13} color="var(--text-light)" />
      <span style={{ fontSize: 16, color: 'var(--text-muted)', fontWeight: 500 }}>{text}</span>
    </div>
  );
}

const s = {
  card: {
    background: 'var(--surface)', borderRadius: 20, boxShadow: 'var(--shadow)',
    border: '1px solid var(--border)', padding: '24px', display: 'flex', alignItems: 'center',
    gap: 24, flexWrap: 'wrap', transition: 'box-shadow .2s ease, transform .2s ease',
  },
  left: { display: 'flex', alignItems: 'center', gap: 18, flex: 1, minWidth: 260 },
  avatar: {
    width: 68, height: 68, borderRadius: '50%', flexShrink: 0,
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 26, fontWeight: 700,
  },
  info: { display: 'flex', flexDirection: 'column', gap: 4 },
  name: { fontSize: 34, fontWeight: 700, color: 'var(--text)', lineHeight: 1.1 },
  role: { fontSize: 20, color: 'var(--primary)', fontWeight: 600 },
  pills: { display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 4 },
  vDivider: { width: 1, height: 72, background: 'var(--border)', flexShrink: 0 },
  scoreBox: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    gap: 6, minWidth: 130,
  },
  scoreLabel: { fontSize: 18, color: 'var(--text-muted)', fontWeight: 500 },
  scoreNum: { fontSize: 44, fontWeight: 700, lineHeight: 1 },
  scoreDenom: { fontSize: 18, fontWeight: 500, color: 'var(--text-muted)' },
  recBadge: {
    borderRadius: 999, padding: '8px 18px', fontSize: 18, fontWeight: 600,
  },
};
