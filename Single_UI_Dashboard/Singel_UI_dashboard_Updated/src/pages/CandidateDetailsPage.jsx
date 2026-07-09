import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Mail, Phone, MapPin, Briefcase, Star, VideoOff, MessageSquare } from 'lucide-react';
import { getAdamCandidateById, getAdamJobById, getInterviewById } from '../data/adamDashboardData';

const SCORE_MAX = 10;

export default function CandidateDetailsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const candidate = getAdamCandidateById(id);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [justificationOpen, setJustificationOpen] = useState(false);

  if (!candidate) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
        Candidate not found.{' '}
        <button style={s.backBtn} onClick={() => navigate('/candidates')}>
          <ArrowLeft size={14} /> Back to Candidates
        </button>
      </div>
    );
  }

  const job = getAdamJobById(candidate.jobId);
  const interview = getInterviewById(candidate.interviewId);

  return (
    <div style={s.page}>
      <div style={s.shell}>
        {/* Back + header */}
        <div style={s.topRow}>
          <button style={s.backBtn} onClick={() => navigate(-1)}>
            <ArrowLeft size={15} /> Back
          </button>
          <p style={s.kicker}>Candidate Details</p>
        </div>

        <div style={s.heroCard}>
          <div style={s.heroLeft}>
            <div style={s.avatar}>{candidate.name[0]}</div>
            <div>
              <h1 style={s.candidateName}>{candidate.name}</h1>
              <p style={s.candidateRole}>{candidate.personalInfo?.currentRole ?? '-'}</p>
              <div style={s.metaRow}>
                <MetaChip icon={MapPin} text={candidate.personalInfo?.location ?? '-'} />
                <MetaChip icon={Briefcase} text={candidate.personalInfo?.experience ?? '-'} />
                <MetaChip icon={Mail} text={candidate.email} />
                <MetaChip icon={Phone} text={candidate.phone} />
              </div>
            </div>
          </div>
          <div style={s.heroRight}>
            <div style={s.workflowBadgeLabel}>Workflow Status</div>
            <span style={{ ...s.stageBadge, ...getStageTone(candidate.workflowStage) }}>
              {candidate.workflowStatus}
            </span>
            {candidate.overallInterviewScore != null && (
              <div style={s.scoreBox}>
                <div style={s.scoreBoxLabel}><Star size={14} /> Overall Score</div>
                <div style={s.scoreValue}>
                  {candidate.overallInterviewScore}
                  <span style={s.scoreDenom}>/10</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div style={s.grid} className="candidate-details-grid">
          <div style={s.leftCol}>
            {/* Resume Summary */}
            <Section title="Resume Summary">
              <p style={s.bodyText}>{candidate.resumeSummary ?? '-'}</p>
            </Section>

            {/* Job Details */}
            {job && (
              <Section title="Job Details">
                <div style={s.infoGrid}>
                  <InfoRow label="Job Title" value={job.title} />
                  <InfoRow label="Company" value={job.companyName} />
                  <InfoRow label="Department" value={job.department ?? '-'} />
                  <InfoRow label="Employment Type" value={job.employmentType} />
                  <InfoRow label="Location" value={job.location} />
                  <InfoRow label="Experience Required" value={job.experienceRequired} />
                  <InfoRow label="Job Status" value={job.status} />
                </div>
              </Section>
            )}

            {/* Communication History */}
            {candidate.communicationHistory?.length > 0 && (
              <Section title="Communication History" icon={<MessageSquare size={16} color="var(--primary)" />}>
                <div style={s.timeline}>
                  {candidate.communicationHistory.map((entry, i) => (
                    <div key={i} style={s.timelineItem}>
                      <div style={s.timelineDot} />
                      <div style={s.timelineContent}>
                        <div style={s.timelineMeta}>
                          <span style={s.timelineDate}>{formatDate(entry.date)}</span>
                          <span style={s.timelineChannel}>{entry.channel}</span>
                        </div>
                        <p style={s.timelineNote}>{entry.note}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </div>

          <div style={s.rightCol}>
            {/* Interview Details */}
            {interview ? (
              <>
                <Section title="Interview Recording">
                  <div style={s.videoFrame}>
                    {interview.videoUrl ? (
                      <video
                        controls
                        playsInline
                        preload="metadata"
                        controlsList="nodownload"
                        src={interview.videoUrl}
                        style={s.video}
                      />
                    ) : (
                      <div style={s.videoPlaceholder}>
                        <VideoOff size={32} color="var(--text-light)" />
                        <span style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8 }}>No recording available</span>
                      </div>
                    )}
                  </div>
                  <div style={s.interviewMeta}>
                    <InfoRow label="Interview ID" value={interview.id} />
                    <InfoRow label="Date" value={formatDate(interview.date)} />
                    <InfoRow label="Duration" value={interview.duration} />
                    <InfoRow label="Recommendation" value={interview.recommendation} />
                  </div>
                </Section>

                <Section
                  title="AI Interview Analysis"
                  action={<button style={s.readMoreBtn} onClick={() => setAnalysisOpen(true)}>Read more</button>}
                >
                  <p style={s.clampedText}>{interview.analysisText ?? '-'}</p>
                </Section>

                <Section title="Scoring Parameters">
                  <div style={s.scoreList}>
                    {interview.scores?.map((item) => (
                      <ScoreBar key={item.label} label={item.label} score={item.score} />
                    ))}
                  </div>
                </Section>

                <Section
                  title="Score Justification"
                  action={<button style={s.readMoreBtn} onClick={() => setJustificationOpen(true)}>Read more</button>}
                >
                  <p style={s.clampedText}>{interview.scoreJustification ?? '-'}</p>
                </Section>
              </>
            ) : (
              <Section title="Interview">
                <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>
                  {candidate.interviewStatus === 'Scheduled'
                    ? 'Interview is scheduled but not yet completed.'
                    : 'No interview data available for this candidate.'}
                </p>
              </Section>
            )}
          </div>
        </div>
      </div>

      {analysisOpen && (
        <TextModal title="AI Interview Analysis" text={interview?.analysisText ?? '-'} onClose={() => setAnalysisOpen(false)} />
      )}
      {justificationOpen && (
        <TextModal title="Score Justification" text={interview?.scoreJustification ?? '-'} onClose={() => setJustificationOpen(false)} />
      )}
    </div>
  );
}

function Section({ title, action, icon, children }) {
  return (
    <div style={s.section}>
      <div style={s.sectionHead}>
        <div style={s.sectionTitleRow}>
          {icon}
          <h3 style={s.sectionTitle}>{title}</h3>
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div style={s.infoRow}>
      <span style={s.infoLabel}>{label}</span>
      <span style={s.infoValue}>{value}</span>
    </div>
  );
}

function MetaChip({ icon: Icon, text }) {
  return (
    <span style={s.chip}>
      <Icon size={12} />
      <span>{text}</span>
    </span>
  );
}

function ScoreBar({ label, score }) {
  const clamped = Math.max(0, Math.min(SCORE_MAX, Number(score) || 0));
  return (
    <div style={s.scoreMetric}>
      <div style={s.scoreMetricTop}>
        <span style={s.scoreMetricLabel}>{label}</span>
        <span style={s.scoreMetricValue}>{clamped.toFixed(1)} / 10</span>
      </div>
      <div style={s.track}>
        <div style={{ ...s.fill, width: `${(clamped / SCORE_MAX) * 100}%` }} />
      </div>
    </div>
  );
}

function TextModal({ title, text, onClose }) {
  return (
    <div style={s.modalOverlay} onClick={onClose} role="presentation">
      <div style={s.modal} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={title}>
        <div style={s.modalHead}>
          <h3 style={s.modalTitle}>{title}</h3>
          <button style={s.modalClose} onClick={onClose} aria-label="Close" type="button">×</button>
        </div>
        <div style={s.modalBody}>{text}</div>
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

function formatDate(date) {
  if (!date) return '-';
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

const s = {
  page: { minHeight: '100vh', background: 'linear-gradient(180deg, #fbfbff 0%, var(--bg) 100%)', padding: '14px 18px 32px' },
  shell: { maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 14 },
  topRow: { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  backBtn: {
    display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--primary)',
    background: 'rgba(91,61,245,0.08)', borderRadius: 999, padding: '9px 14px',
    fontSize: 13, fontWeight: 600,
  },
  kicker: { fontSize: 13, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--primary)' },
  heroCard: {
    background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 20,
    boxShadow: 'var(--shadow)', padding: '20px 24px',
    display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap',
  },
  heroLeft: { display: 'flex', alignItems: 'flex-start', gap: 16, flex: 1, minWidth: 0 },
  avatar: {
    width: 56, height: 56, borderRadius: '50%', flexShrink: 0,
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 20, fontWeight: 800,
  },
  candidateName: { fontSize: 24, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--text)', marginBottom: 4 },
  candidateRole: { fontSize: 14, fontWeight: 600, color: 'var(--primary)', marginBottom: 10 },
  metaRow: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  chip: {
    display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 10px',
    borderRadius: 999, background: 'var(--primary-bg)', color: 'var(--text-body)', fontSize: 12, fontWeight: 600,
  },
  heroRight: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 10, flexShrink: 0 },
  workflowBadgeLabel: { fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' },
  stageBadge: { display: 'inline-flex', alignItems: 'center', borderRadius: 999, padding: '7px 14px', fontSize: 13, fontWeight: 700 },
  scoreBox: {
    padding: '10px 14px', borderRadius: 14, border: '1px solid #d9f7e8',
    background: 'linear-gradient(180deg, rgba(236,253,245,0.95) 0%, rgba(248,255,251,0.96) 100%)',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
  },
  scoreBoxLabel: { display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' },
  scoreValue: { fontSize: 28, fontWeight: 800, color: 'var(--primary)', letterSpacing: '-0.04em' },
  scoreDenom: { fontSize: 13, fontWeight: 400, color: 'var(--text-muted)', marginLeft: 2 },
  grid: { display: 'grid', gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr)', gap: 14, alignItems: 'start' },
  leftCol: { display: 'flex', flexDirection: 'column', gap: 14 },
  rightCol: { display: 'flex', flexDirection: 'column', gap: 14 },
  section: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 20, boxShadow: 'var(--shadow)', padding: '18px 20px' },
  sectionHead: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 14 },
  sectionTitleRow: { display: 'flex', alignItems: 'center', gap: 8 },
  sectionTitle: { fontSize: 16, fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--text)' },
  bodyText: { fontSize: 14.5, lineHeight: 1.7, color: 'var(--text-body)' },
  infoGrid: { display: 'grid', gap: 10 },
  infoRow: { display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 14, borderBottom: '1px solid var(--border)', paddingBottom: 8 },
  infoLabel: { fontWeight: 600, color: 'var(--text-muted)', flexShrink: 0 },
  infoValue: { fontWeight: 500, color: 'var(--text)', textAlign: 'right' },
  timeline: { display: 'flex', flexDirection: 'column', gap: 14 },
  timelineItem: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  timelineDot: { width: 10, height: 10, borderRadius: '50%', background: 'var(--primary)', flexShrink: 0, marginTop: 5 },
  timelineContent: { flex: 1 },
  timelineMeta: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 },
  timelineDate: { fontSize: 12, fontWeight: 700, color: 'var(--text-muted)' },
  timelineChannel: { fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', background: 'var(--primary-bg)', color: 'var(--primary)', borderRadius: 999, padding: '2px 8px' },
  timelineNote: { fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-body)' },
  videoFrame: { width: '100%', aspectRatio: '16 / 7', borderRadius: 14, overflow: 'hidden', background: '#0f172a', marginBottom: 14 },
  video: { width: '100%', height: '100%', display: 'block', objectFit: 'cover' },
  videoPlaceholder: { width: '100%', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)' },
  interviewMeta: { display: 'grid', gap: 8 },
  readMoreBtn: {
    display: 'inline-flex', alignItems: 'center', border: '1px solid rgba(91,61,245,0.18)',
    background: 'rgba(91,61,245,0.08)', color: 'var(--primary)', borderRadius: 999,
    padding: '5px 12px', fontSize: 12, fontWeight: 700,
  },
  clampedText: {
    fontSize: 14, lineHeight: 1.7, color: 'var(--text-body)',
    display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical', overflow: 'hidden',
  },
  scoreList: { display: 'grid', gap: 10 },
  scoreMetric: { display: 'grid', gap: 5 },
  scoreMetricTop: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  scoreMetricLabel: { fontSize: 13, fontWeight: 600, color: 'var(--text-body)' },
  scoreMetricValue: { fontSize: 13, fontWeight: 700, color: 'var(--text)', whiteSpace: 'nowrap' },
  track: { height: 6, background: '#e5e7eb', borderRadius: 999, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 999, background: 'var(--primary)', transition: 'width .7s ease' },
  modalOverlay: { position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18, zIndex: 1000 },
  modal: { width: 'min(880px, 100%)', maxHeight: 'min(82vh, 860px)', overflow: 'auto', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 20, boxShadow: '0 24px 60px rgba(15,23,42,0.28)', padding: 18 },
  modalHead: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 14 },
  modalTitle: { fontSize: 20, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--text)' },
  modalClose: { border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-muted)', borderRadius: 999, width: 32, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0 },
  modalBody: { fontSize: 14.5, lineHeight: 1.75, color: 'var(--text-body)', whiteSpace: 'pre-wrap' },
};
