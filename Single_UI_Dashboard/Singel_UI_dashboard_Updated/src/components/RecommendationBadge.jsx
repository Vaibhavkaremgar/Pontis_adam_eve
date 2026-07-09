const CONFIG = {
  'Strong Hire': { bg: 'var(--success-bg)', color: 'var(--success)' },
  'Hire':        { bg: 'var(--primary-bg)', color: 'var(--primary)' },
  'Hold':        { bg: 'var(--warning-bg)', color: 'var(--warning)' },
  'Reject':      { bg: 'var(--danger-bg)', color: 'var(--danger)' },
};

export default function RecommendationBadge({ recommendation, large = false }) {
  const cfg = CONFIG[recommendation] ?? { bg: '#f1f5f9', color: '#475569' };
  return (
    <span style={{
      background: cfg.bg, color: cfg.color,
      borderRadius: 999, padding: large ? '8px 18px' : '4px 10px',
      fontSize: large ? 18 : 12, fontWeight: 600,
      display: 'inline-block',
    }}>
      {recommendation}
    </span>
  );
}
