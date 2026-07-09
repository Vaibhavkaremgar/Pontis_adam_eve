import { useNavigate } from 'react-router-dom';
import { Briefcase, ChevronRight, MapPin, Building2 } from 'lucide-react';
import { getAdamJobs } from '../data/adamDashboardData';

export default function JobsPage() {
  const navigate = useNavigate();
  const jobs = getAdamJobs();

  return (
    <div style={s.page}>
      <div style={s.header}>
        <div>
          <p style={s.kicker}>Product Owner Workspace</p>
          <h1 style={s.title}>Jobs</h1>
          <p style={s.sub}>Only ADAM-created jobs are listed here. Dashboard jobs remain hidden.</p>
        </div>
        <div style={s.badge}>{jobs.length} ADAM jobs</div>
      </div>

      <div style={s.card}>
        <div style={s.cardHead}>
          <div style={s.cardTitleRow}>
            <Briefcase size={18} color="var(--primary)" />
            <h2 style={s.cardTitle}>Job Pipeline</h2>
          </div>
          <span style={s.cardHint}>Select a job to review its candidates</span>
        </div>

        <div style={s.tableWrap}>
          <table style={s.table}>
            <thead>
              <tr>
                {[
                  'Job Title',
                  'Company Name',
                  'Department',
                  'Employment Type',
                  'Location',
                  'Experience Required',
                  'Job Status',
                  'Created Date',
                  '',
                ].map((label) => (
                  <th key={label || 'actions'} style={s.th}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} style={s.tr} onClick={() => navigate(`/candidates?jobId=${encodeURIComponent(job.id)}`)}>
                  <td style={s.td}>
                    <div style={s.jobCell}>
                      <div style={s.jobAvatar}>{job.title.split(' ').map((part) => part[0]).slice(0, 2).join('')}</div>
                      <div>
                        <div style={s.primaryText}>{job.title}</div>
                        <div style={s.secondaryText}>{job.id}</div>
                      </div>
                    </div>
                  </td>
                  <td style={s.td}>
                    <div style={s.metaCell}>
                      <Building2 size={14} color="var(--text-light)" />
                      <span>{job.companyName}</span>
                    </div>
                  </td>
                  <td style={s.td}>{job.department || '-'}</td>
                  <td style={s.td}>{job.employmentType}</td>
                  <td style={s.td}>
                    <div style={s.metaCell}>
                      <MapPin size={14} color="var(--text-light)" />
                      <span>{job.location}</span>
                    </div>
                  </td>
                  <td style={s.td}>{job.experienceRequired}</td>
                  <td style={s.td}>
                    <span style={{ ...s.status, ...getStatusTone(job.status) }}>{job.status}</span>
                  </td>
                  <td style={s.td}>{formatDate(job.createdAt)}</td>
                  <td style={s.td}>
                    <button
                      type="button"
                      style={s.viewBtn}
                      onClick={(event) => {
                        event.stopPropagation();
                        navigate(`/candidates?jobId=${encodeURIComponent(job.id)}`);
                      }}
                    >
                      View candidates
                      <ChevronRight size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function getStatusTone(status) {
  switch (status) {
    case 'Open':
      return { background: 'var(--success-bg)', color: 'var(--success)' };
    case 'In Review':
      return { background: 'var(--warning-bg)', color: 'var(--warning)' };
    case 'Closed':
      return { background: 'var(--danger-bg)', color: 'var(--danger)' };
    default:
      return { background: '#f3f4f6', color: 'var(--text-muted)' };
  }
}

function formatDate(date) {
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

const s = {
  page: {
    maxWidth: 1500,
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 18,
    flexWrap: 'wrap',
  },
  kicker: {
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    color: 'var(--primary)',
    marginBottom: 6,
  },
  title: {
    fontSize: 38,
    fontWeight: 800,
    letterSpacing: '-0.04em',
    color: 'var(--text)',
    lineHeight: 1.05,
  },
  sub: {
    fontSize: 16,
    color: 'var(--text-muted)',
    marginTop: 8,
  },
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    borderRadius: 999,
    padding: '10px 16px',
    background: 'var(--primary-bg)',
    color: 'var(--primary)',
    fontSize: 13,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    boxShadow: 'var(--shadow)',
    padding: 14,
    overflow: 'hidden',
  },
  cardHead: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    flexWrap: 'wrap',
    padding: '4px 4px 14px',
  },
  cardTitleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: 800,
    color: 'var(--text)',
  },
  cardHint: {
    fontSize: 12.5,
    color: 'var(--text-muted)',
  },
  tableWrap: {
    width: '100%',
    overflowX: 'auto',
    borderRadius: 16,
    border: '1px solid var(--border)',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    minWidth: 1120,
  },
  th: {
    fontSize: 13,
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    color: 'var(--text-muted)',
    background: '#fafafa',
    padding: '15px 16px',
    textAlign: 'left',
    borderBottom: '1px solid var(--border)',
  },
  tr: {
    transition: 'background .15s ease',
    cursor: 'pointer',
  },
  td: {
    padding: '16px',
    borderBottom: '1px solid var(--border)',
    color: 'var(--text-body)',
    fontSize: 14.5,
    verticalAlign: 'middle',
    whiteSpace: 'nowrap',
  },
  jobCell: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    minWidth: 220,
  },
  jobAvatar: {
    width: 42,
    height: 42,
    borderRadius: 14,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff',
    fontSize: 13,
    fontWeight: 800,
    flexShrink: 0,
  },
  primaryText: {
    fontSize: 15,
    fontWeight: 700,
    color: 'var(--text)',
  },
  secondaryText: {
    fontSize: 12.5,
    color: 'var(--text-muted)',
    marginTop: 2,
  },
  metaCell: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
  },
  status: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 999,
    padding: '6px 12px',
    fontSize: 12.5,
    fontWeight: 700,
  },
  viewBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    border: '1px solid rgba(91,61,245,0.18)',
    background: 'rgba(91,61,245,0.08)',
    color: 'var(--primary)',
    padding: '7px 12px',
    fontSize: 12.5,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },
};
