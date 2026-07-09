import { useNavigate } from 'react-router-dom';
import { useInterviewList } from '../hooks/useInterview';
import { Eye } from 'lucide-react';

const STATUS = {
  'Completed': { bg: 'var(--success-bg)', color: 'var(--success)' },
  'Under Review': { bg: 'var(--warning-bg)', color: 'var(--warning)' },
  'Scheduled': { bg: 'var(--primary-bg)', color: 'var(--primary)' },
};

function scoreColor() {
  return 'var(--primary)';
}

export default function InterviewsPage() {
  const { candidates, loading } = useInterviewList();
  const navigate = useNavigate();

  return (
    <div style={s.page}>
      <div style={s.pageHeader}>
        <div>
          <h1 style={s.title}>Interviews</h1>
          <p style={s.sub}>AI-powered candidate evaluation reports</p>
        </div>
        <div style={s.countBadge}>{loading ? '-' : `${candidates.length} interviews`}</div>
      </div>

      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead>
            <tr>
              {['Candidate', 'Job Role', 'Interview Date', 'Overall Score', 'Status', 'Actions'].map((h) => (
                <th key={h} style={s.th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading
              ? [1, 2, 3].map((i) => (
                  <tr key={i}>
                    {[1, 2, 3, 4, 5, 6, 7].map((j) => (
                      <td key={j} style={s.td}>
                        <div className="skeleton" style={{ height: 14, width: j === 7 ? 90 : '80%', borderRadius: 8 }} />
                      </td>
                    ))}
                  </tr>
                ))
              : candidates.map((c) => {
                  const status = STATUS[c.status] ?? { bg: '#f3f4f6', color: 'var(--text-muted)' };
                  const sc = scoreColor(c.overallScore);
                  const formatted = new Date(c.date).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

                  return (
                    <tr key={c.id} style={s.tr} onMouseEnter={(e) => e.currentTarget.style.background = '#fafafa'} onMouseLeave={(e) => e.currentTarget.style.background = '#fff'}>
                      <td style={s.td}>
                        <div style={s.candidateCell}>
                          <div style={s.avatar}>{c.candidate.name[0]}</div>
                          <div>
                            <div style={s.candidateName}>{c.candidate.name}</div>
                            <div style={s.candidateSub}>{c.id}</div>
                          </div>
                        </div>
                      </td>
                      <td style={s.td}><span style={s.cellText}>{c.candidate.role}</span></td>
                      <td style={s.td}><span style={s.cellText}>{formatted}</span></td>
                      <td style={s.td}>
                        <span style={{ ...s.scorePill, color: sc, background: 'var(--primary-bg)' }}>
                          {c.overallScore} <span style={{ opacity: 0.5, fontWeight: 400 }}>/10</span>
                        </span>
                      </td>
                      <td style={s.td}>
                        <span style={{ ...s.badge, background: status.bg, color: status.color }}>{c.status}</span>
                      </td>
                      <td style={s.td}>
                        <button style={s.viewBtn} onClick={() => navigate(`/interviews/${c.id}`)}>
                          <Eye size={13} />
                          View Report
                        </button>
                      </td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const s = {
  page: { maxWidth: 1300, margin: '0 auto', padding: 0 },
  pageHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 24, marginBottom: 24, flexWrap: 'wrap' },
  title: { fontSize: 40, fontWeight: 700, color: 'var(--text)' },
  sub: { fontSize: 18, fontWeight: 400, color: 'var(--text-muted)', marginTop: 6 },
  countBadge: { fontSize: 14, fontWeight: 600, color: 'var(--primary)', background: 'var(--primary-bg)', borderRadius: 999, padding: '8px 18px' },
  tableWrap: { background: 'var(--surface)', borderRadius: 20, boxShadow: 'var(--shadow)', border: '1px solid var(--border)', overflow: 'hidden' },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: {
    fontSize: 14,
    fontWeight: 600,
    color: 'var(--text-muted)',
    padding: '18px 20px',
    textAlign: 'left',
    borderBottom: '1px solid var(--border)',
    background: '#fafafa',
  },
  tr: { background: '#fff', transition: 'background .15s' },
  td: { padding: '18px 20px', borderBottom: '1px solid var(--border)', verticalAlign: 'middle' },
  candidateCell: { display: 'flex', alignItems: 'center', gap: 12 },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: '50%',
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    fontWeight: 700,
    flexShrink: 0,
  },
  candidateName: { fontSize: 16, fontWeight: 600, color: 'var(--text)' },
  candidateSub: { fontSize: 14, color: 'var(--text-muted)', marginTop: 2 },
  cellText: { fontSize: 16, color: 'var(--text-body)' },
  scorePill: { fontSize: 16, fontWeight: 700, borderRadius: 999, padding: '5px 12px' },
  badge: { fontSize: 12, fontWeight: 600, borderRadius: 999, padding: '5px 12px' },
  viewBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 14,
    fontWeight: 600,
    color: '#fff',
    background: 'var(--primary)',
    border: 'none',
    borderRadius: 12,
    padding: '9px 14px',
    cursor: 'pointer',
    boxShadow: '0 10px 18px rgba(91, 61, 245, 0.18)',
  },
};
