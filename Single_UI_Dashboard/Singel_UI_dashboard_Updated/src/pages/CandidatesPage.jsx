import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Users, ChevronRight, Mail, Phone } from 'lucide-react';
import { getAdamCandidates, getAdamJobById, WORKFLOW_STAGES } from '../data/adamDashboardData';

export default function CandidatesPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const jobIdFilter = searchParams.get('jobId');
  const [stageFilter, setStageFilter] = useState('all');

  const allCandidates = getAdamCandidates();
  const candidates = allCandidates.filter((c) => {
    const matchesJob = !jobIdFilter || c.jobId === jobIdFilter;
    const matchesStage = stageFilter === 'all' || c.workflowStage === stageFilter;
    return matchesJob && matchesStage;
  });

  const jobTitle = jobIdFilter ? getAdamJobById(jobIdFilter)?.title : null;

  return (
    <div style={s.page}>
      <div style={s.header}>
        <div>
          <p style={s.kicker}>Product Owner Workspace</p>
          <h1 style={s.title}>Candidates</h1>
          <p style={s.sub}>
            {jobTitle ? `Showing candidates for: ${jobTitle}` : 'Candidates selected by Client/User from ADAM recommendations'}
          </p>
        </div>
        <div style={s.badge}>{candidates.length} candidate{candidates.length !== 1 ? 's' : ''}</div>
      </div>

      <div style={s.card}>
        <div style={s.cardHead}>
          <div style={s.cardTitleRow}>
            <Users size={18} color="var(--primary)" />
            <h2 style={s.cardTitle}>Recruitment Pipeline</h2>
          </div>
          <div style={s.filters}>
            <select
              style={s.select}
              value={stageFilter}
              onChange={(e) => setStageFilter(e.target.value)}
            >
              {WORKFLOW_STAGES.map((stage) => (
                <option key={stage.value} value={stage.value}>{stage.label}</option>
              ))}
            </select>
            {jobIdFilter && (
              <button style={s.clearBtn} onClick={() => navigate('/candidates')}>
                Clear job filter
              </button>
            )}
          </div>
        </div>

        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead>
              <tr>
                {['Candidate', 'Email', 'Phone', 'Job Applied For', 'Workflow Status', 'Interview Status', 'Score', ''].map((h) => (
                  <th key={h || 'actions'} style={s.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {candidates.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ ...s.td, textAlign: 'center', color: 'var(--text-muted)', padding: 32 }}>
                    No candidates match the selected filters.
                  </td>
                </tr>
              ) : candidates.map((c) => {
                const job = getAdamJobById(c.jobId);
                return (
                  <tr
                    key={c.id}
                    style={s.tr}
                    onClick={() => navigate(`/candidates/${c.id}`)}
                    onMouseEnter={(e) => e.currentTarget.style.background = '#fafafa'}
                    onMouseLeave={(e) => e.currentTarget.style.background = '#fff'}
                  >
                    <td style={s.td}>
                      <div style={s.candidateCell}>
                        <div style={s.avatar}>{c.name[0]}</div>
                        <div>
                          <div style={s.primaryText}>{c.name}</div>
                          <div style={s.secondaryText}>{c.id}</div>
                        </div>
                      </div>
                    </td>
                    <td style={s.td}>
                      <div style={s.metaCell}>
                        <Mail size={13} color="var(--text-light)" />
                        <span>{c.email}</span>
                      </div>
                    </td>
                    <td style={s.td}>
                      <div style={s.metaCell}>
                        <Phone size={13} color="var(--text-light)" />
                        <span>{c.phone}</span>
                      </div>
                    </td>
                    <td style={s.td}>{job?.title ?? '-'}</td>
                    <td style={s.td}>
                      <span style={{ ...s.stageBadge, ...getStageTone(c.workflowStage) }}>{c.workflowStatus}</span>
                    </td>
                    <td style={s.td}>
                      <span style={{ ...s.stageBadge, ...getInterviewTone(c.interviewStatus) }}>{c.interviewStatus ?? '-'}</span>
                    </td>
                    <td style={s.td}>
                      {c.overallInterviewScore != null
                        ? <span style={s.scorePill}>{c.overallInterviewScore}<span style={s.scoreDenom}>/10</span></span>
                        : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                    </td>
                    <td style={s.td}>
                      <button
                        type="button"
                        style={s.viewBtn}
                        onClick={(e) => { e.stopPropagation(); navigate(`/candidates/${c.id}`); }}
                      >
                        View details
                        <ChevronRight size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function getStageTone(stage) {
  switch (stage) {
    case 'shortlisted': return { background: 'var(--success-bg)', color: 'var(--success)' };
    case 'interview_completed': return { background: 'var(--primary-bg)', color: 'var(--primary)' };
    case 'interview_scheduled': return { background: 'var(--primary-bg)', color: 'var(--primary)' };
    case 'rejected': return { background: 'var(--danger-bg)', color: 'var(--danger)' };
    case 'responded': return { background: 'var(--warning-bg)', color: 'var(--warning)' };
    default: return { background: '#f3f4f6', color: 'var(--text-muted)' };
  }
}

function getInterviewTone(status) {
  switch (status) {
    case 'Completed': return { background: 'var(--success-bg)', color: 'var(--success)' };
    case 'Scheduled': return { background: 'var(--primary-bg)', color: 'var(--primary)' };
    default: return { background: '#f3f4f6', color: 'var(--text-muted)' };
  }
}

const s = {
  page: { maxWidth: 1500, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 },
  header: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 18, flexWrap: 'wrap' },
  kicker: { fontSize: 12, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--primary)', marginBottom: 6 },
  title: { fontSize: 38, fontWeight: 800, letterSpacing: '-0.04em', color: 'var(--text)', lineHeight: 1.05 },
  sub: { fontSize: 16, color: 'var(--text-muted)', marginTop: 8 },
  badge: { display: 'inline-flex', alignItems: 'center', borderRadius: 999, padding: '10px 16px', background: 'var(--primary-bg)', color: 'var(--primary)', fontSize: 13, fontWeight: 700, whiteSpace: 'nowrap' },
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 20, boxShadow: 'var(--shadow)', padding: 14, overflow: 'hidden' },
  cardHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', padding: '4px 4px 14px' },
  cardTitleRow: { display: 'flex', alignItems: 'center', gap: 10 },
  cardTitle: { fontSize: 18, fontWeight: 800, color: 'var(--text)' },
  filters: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  select: {
    padding: '8px 12px', borderRadius: 10, border: '1px solid var(--border)',
    background: 'var(--surface)', color: 'var(--text)', fontSize: 13, fontWeight: 500,
    cursor: 'pointer', outline: 'none',
  },
  clearBtn: {
    padding: '8px 14px', borderRadius: 999, border: '1px solid rgba(91,61,245,0.18)',
    background: 'rgba(91,61,245,0.08)', color: 'var(--primary)', fontSize: 12.5, fontWeight: 700,
  },
  tableWrap: { width: '100%', overflowX: 'auto', borderRadius: 16, border: '1px solid var(--border)' },
  table: { width: '100%', borderCollapse: 'collapse', minWidth: 1100 },
  th: { fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-muted)', background: '#fafafa', padding: '15px 16px', textAlign: 'left', borderBottom: '1px solid var(--border)' },
  tr: { background: '#fff', transition: 'background .15s', cursor: 'pointer' },
  td: { padding: '16px', borderBottom: '1px solid var(--border)', color: 'var(--text-body)', fontSize: 14.5, verticalAlign: 'middle', whiteSpace: 'nowrap' },
  candidateCell: { display: 'flex', alignItems: 'center', gap: 12, minWidth: 180 },
  avatar: {
    width: 40, height: 40, borderRadius: '50%', flexShrink: 0,
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 14, fontWeight: 700,
  },
  primaryText: { fontSize: 15, fontWeight: 700, color: 'var(--text)' },
  secondaryText: { fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 },
  metaCell: { display: 'inline-flex', alignItems: 'center', gap: 8 },
  stageBadge: { display: 'inline-flex', alignItems: 'center', borderRadius: 999, padding: '5px 12px', fontSize: 12.5, fontWeight: 700 },
  scorePill: { fontSize: 15, fontWeight: 700, color: 'var(--primary)' },
  scoreDenom: { fontSize: 12, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 1 },
  viewBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 6, borderRadius: 999,
    border: '1px solid rgba(91,61,245,0.18)', background: 'rgba(91,61,245,0.08)',
    color: 'var(--primary)', padding: '7px 12px', fontSize: 12.5, fontWeight: 700, whiteSpace: 'nowrap',
  },
};
