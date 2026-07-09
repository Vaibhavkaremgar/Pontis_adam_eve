import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Briefcase,
  MapPin,
  Star,
  VideoOff,
} from 'lucide-react';
import { useInterviewById } from '../hooks/useInterview';

const SCORE_MAX = 10;

export default function InterviewReportPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data: interview, loading } = useInterviewById(id);
  const [analysisModalOpen, setAnalysisModalOpen] = useState(false);
  const [justificationModalOpen, setJustificationModalOpen] = useState(false);

  const candidate = interview?.candidate ?? {};
  const scoreRows = interview?.scores ?? [];
  const formattedDate = formatDate(interview?.date);
  const analysisParagraph = useMemo(() => formatParagraph(interview?.analysisText), [interview?.analysisText]);
  const highlightScores = useMemo(() => buildHighlightScores(scoreRows), [scoreRows]);

  return (
    <div style={s.page} className="interview-executive-report interview-report-page">
      <div style={s.shell}>
        <header style={s.topbar} className="report-header-grid">
          <div style={s.topbarLeft}>
            <div style={s.backLine}>
              <button style={s.backBtn} onClick={() => navigate('/interviews')}>
                <ArrowLeft size={15} />
                Back to interviews
              </button>
              <p style={s.kicker}>Interview Analysis</p>
            </div>

            {loading ? (
              <div style={s.summaryCopySkeleton}>
                <div style={s.summaryIdentitySkeleton}>
                  <div className="skeleton" style={{ width: 260, height: 30, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: 180, height: 18 }} />
                </div>
                <div style={s.summaryMetaRowSkeleton}>
                  <div className="skeleton" style={s.metaItemSkeleton} />
                  <div className="skeleton" style={s.metaItemSkeleton} />
                  <div className="skeleton" style={s.metaItemSkeleton} />
                </div>
                <div style={s.chipRowSkeleton}>
                  <div className="skeleton" style={s.chipSkeleton} />
                  <div className="skeleton" style={s.chipSkeleton} />
                </div>
              </div>
            ) : (
              <div style={s.summaryLeft}>
                <div style={s.summaryCopy}>
                  <div style={s.summaryIdentity}>
                    <h2 style={s.candidateName}>{candidate.name ?? '-'}</h2>
                    <div style={s.summaryTitleRow}>
                      <p style={s.candidateRole}>{candidate.role ?? '-'}</p>
                      <div style={s.chipRowCompact}>
                        <MetaChip icon={Briefcase} text={candidate.experience ?? '-'} />
                        <MetaChip icon={MapPin} text={candidate.location ?? '-'} />
                      </div>
                    </div>
                  </div>
                  <div style={s.summaryMetaRow}>
                    <InlineMeta label="Interview ID" value={interview?.id ?? '-'} />
                    <InlineMeta label="Date" value={formattedDate} />
                    <InlineMeta label="Duration" value={interview?.duration ?? '-'} />
                  </div>
                </div>
              </div>
            )}
          </div>

          <div style={s.summaryScorePanel}>
            <div style={s.summaryScoreHeader}>
              <div style={s.summaryScoreIcon}>
                <Star size={18} fill="currentColor" />
              </div>
              <div style={s.scoreLabel}>Overall Score</div>
            </div>
            <div style={s.summaryScoreRow}>
              <div style={s.summaryScoreValue}>
                {formatScore(interview?.overallScore ?? 0)}
                <span style={s.scoreDenom}>/10</span>
              </div>
              <RecommendationBadge recommendation={interview?.recommendation ?? '-'} />
            </div>
            <div style={s.summaryScoreHint}>
              Final recommendation and score remain visible beside the candidate summary.
            </div>
          </div>
        </header>

        <section style={s.mainGrid} className="report-bottom-grid interview-report-columns">
          <div style={s.leftColumn}>
            <section style={s.videoCard}>
              <div style={s.videoFrame}>
                {loading ? (
                  <div className="skeleton" style={s.videoSkeleton} />
                ) : interview?.videoUrl ? (
                  <video
                    controls
                    playsInline
                    preload="metadata"
                    controlsList="nodownload"
                    src={interview.videoUrl}
                    style={s.video}
                  />
                ) : (
                  <div style={s.placeholder}>
                    <div style={s.placeholderIcon}>
                      <VideoOff size={36} color="var(--text-light)" />
                    </div>
                    <div style={s.placeholderTitle}>No recording available</div>
                    <div style={s.placeholderText}>
                      The interview video has not been uploaded for this session yet.
                    </div>
                  </div>
                )}
              </div>
            </section>

            <PanelCard
              title="AI Interview Analysis"
              subtitle="A concise summary of communication, technical depth, problem solving, and overall interview takeaways."
              action={<button type="button" style={s.panelActionBtn} onClick={() => setAnalysisModalOpen(true)}>Read more</button>}
            >
              {loading ? (
                <div>
                  <div className="skeleton" style={{ width: '100%', height: 14, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: '96%', height: 14, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: '94%', height: 14, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: '84%', height: 14 }} />
                </div>
              ) : (
                <>
                  <p style={s.narrativePreview}>{analysisParagraph || interview?.analysisText || '-'}</p>
                </>
              )}
            </PanelCard>
          </div>

          <div style={s.rightColumn} className="report-right-column interview-report-scoring-panel">
            <PanelCard
              title="Scoring Parameters"
              subtitle="High-level scoring summary using the existing backend values."
            >
              <div style={s.scoreList}>
                {loading
                  ? Array.from({ length: 4 }).map((_, index) => (
                      <div key={index} style={s.scoreMetric}>
                        <div style={s.scoreMetricTop}>
                          <div className="skeleton" style={{ width: 150, height: 14 }} />
                          <div className="skeleton" style={{ width: 48, height: 14 }} />
                        </div>
                        <div className="skeleton" style={{ width: '100%', height: 8, borderRadius: 999 }} />
                        <div className="skeleton" style={{ width: '88%', height: 13, marginTop: 10 }} />
                        <div className="skeleton" style={{ width: '74%', height: 13, marginTop: 8 }} />
                      </div>
                    ))
                  : highlightScores.map((item) => (
                      <ScoreMetric key={item.label} {...item} />
                    ))}
              </div>
            </PanelCard>

            <PanelCard
              title="Score Justification"
              subtitle="Why the candidate received this overall score."
              action={<button type="button" style={s.panelActionBtn} onClick={() => setJustificationModalOpen(true)}>Read more</button>}
            >
              {loading ? (
                <div>
                  <div className="skeleton" style={{ width: '100%', height: 14, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: '98%', height: 14, marginBottom: 10 }} />
                  <div className="skeleton" style={{ width: '86%', height: 14 }} />
                </div>
              ) : (
                <>
                  <p style={s.narrativePreview}>{interview?.scoreJustification ?? '-'}</p>
                </>
              )}
            </PanelCard>
          </div>
        </section>

        {analysisModalOpen ? (
          <TextModal
            title="AI Interview Analysis"
            text={analysisParagraph || interview?.analysisText || '-'}
            onClose={() => setAnalysisModalOpen(false)}
          />
        ) : null}

        {justificationModalOpen ? (
          <TextModal
            title="Score Justification"
            text={interview?.scoreJustification ?? '-'}
            onClose={() => setJustificationModalOpen(false)}
          />
        ) : null}

        <footer style={s.footer}>
          <div style={s.footerLeft}>
            <div style={s.footerBrand}>Pontis</div>
            <span>This analysis is generated by Pontis AI Interview Agent</span>
          </div>
          <div style={s.footerRight}>Empowering smarter hiring decisions</div>
        </footer>
      </div>
    </div>
  );
}

function PanelCard({ title, subtitle, action, children }) {
  return (
    <section style={s.panel}>
      <div style={s.panelHead}>
        <div>
          <h3 style={s.panelTitle}>{title}</h3>
          <p style={s.panelSub}>{subtitle}</p>
        </div>
        {action ? <div style={s.panelActionWrap}>{action}</div> : null}
      </div>
      {children}
    </section>
  );
}

function TextModal({ title, text, onClose }) {
  return (
    <div style={s.modalOverlay} onClick={onClose} role="presentation">
      <div style={s.modal} onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={title}>
        <div style={s.modalHead}>
          <div>
            <h3 style={s.modalTitle}>{title}</h3>
          </div>
          <button style={s.modalClose} onClick={onClose} aria-label="Close modal" type="button">
            ×
          </button>
        </div>
        <div style={s.modalBody}>{text}</div>
      </div>
    </div>
  );
}

function InlineMeta({ label, value }) {
  return (
    <div style={s.inlineMetaItem}>
      <span style={s.inlineMetaLabel}>{label}:</span>
      <span style={s.inlineMetaValue}>{value}</span>
    </div>
  );
}

function MetaChip({ icon: Icon, text }) {
  return (
    <span style={s.chip}>
      <Icon size={13} />
      <span>{text}</span>
    </span>
  );
}

function RecommendationBadge({ recommendation }) {
  const tone = getRecommendationTone(recommendation);

  return (
    <span
      style={{
        ...s.recommendation,
        background: tone.bg,
        color: tone.color,
        borderColor: tone.border,
      }}
    >
      {recommendation}
    </span>
  );
}

function ScoreMetric({ label, score, description }) {
  const clamped = clampScore(score);

  return (
    <div style={s.scoreMetric}>
      <div style={s.scoreMetricTop}>
        <div style={s.scoreMetricLabel}>{label}</div>
        <div style={s.scoreMetricValue}>{formatScore(clamped)} / 10</div>
      </div>
      <div style={s.track}>
        <div style={{ ...s.fill, width: `${(clamped / SCORE_MAX) * 100}%` }} />
      </div>
      <p style={s.scoreMetricText}>{description}</p>
    </div>
  );
}

function buildHighlightScores(scoreRows) {
  const communication = findScore(scoreRows, ['Communication']);
  const technical = findScore(scoreRows, ['Technical Skills', 'Technical Knowledge', 'Coding Skills', 'Coding & Practical Skills']);
  const cultureFit = findScore(scoreRows, ['Culture Fit']);
  const overall = findScore(scoreRows, ['Overall', 'Overall Impression']);

  return [
    {
      label: 'Communication',
      score: communication,
      description: buildScoreDescription('Communication', communication),
    },
    {
      label: 'Technical Skills',
      score: technical,
      description: buildScoreDescription('Technical Skills', technical),
    },
    {
      label: 'Culture Fit',
      score: cultureFit,
      description: buildScoreDescription('Culture Fit', cultureFit),
    },
    {
      label: 'Overall',
      score: overall,
      description: buildScoreDescription('Overall', overall),
    },
  ];
}

function buildScoreDescription(label, score) {
  const value = clampScore(score);
  const band = value >= 8 ? 'strong' : value >= 6.5 ? 'solid' : 'developing';

  switch (label) {
    case 'Communication':
      return `${band === 'strong' ? 'Clear, structured, and confident responses.' : band === 'solid' ? 'Communicates ideas clearly with a few moments of hesitation.' : 'Communication is understandable, but the delivery needs more structure and clarity.'}`;
    case 'Technical Skills':
      return `${band === 'strong' ? 'Shows strong technical depth and practical problem solving.' : band === 'solid' ? 'Demonstrates workable technical grounding with room for deeper detail.' : 'Technical depth is present, but broader problem-solving confidence would help.'}`;
    case 'Culture Fit':
      return `${band === 'strong' ? 'Aligned with team expectations, collaboration, and ownership.' : band === 'solid' ? 'Shows a reasonable fit with the working style and values of the team.' : 'Fit is possible, but alignment with the role and team rhythm is still developing.'}`;
    case 'Overall':
    default:
      return `${band === 'strong' ? 'A well-rounded interview with strong execution across the key dimensions.' : band === 'solid' ? 'A balanced performance with a few areas worth strengthening.' : 'The overall interview shows promise, but several fundamentals need more polish.'}`;
  }
}

function findScore(scoreRows, labels) {
  for (const label of labels) {
    const match = scoreRows.find((row) => row.label === label);
    if (match) return match.score;
  }
  return 0;
}

function formatParagraph(text) {
  return String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildCandidateAvatar(name, role) {
  const safeName = String(name ?? 'Candidate');
  const safeRole = String(role ?? 'Interview candidate');
  const initials = safeName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('') || 'C';
  const palette = pickPalette(`${safeName}|${safeRole}`);

  const safeLabel = escapeXml(safeName);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" role="img" aria-label="${safeLabel}">
      <defs>
        <linearGradient id="bg" x1="0%" x2="100%" y1="0%" y2="100%">
          <stop offset="0%" stop-color="${palette[0]}"/>
          <stop offset="100%" stop-color="${palette[1]}"/>
        </linearGradient>
        <radialGradient id="glow" cx="38%" cy="30%" r="68%">
          <stop offset="0%" stop-color="rgba(255,255,255,0.55)"/>
          <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
        </radialGradient>
      </defs>
      <rect width="240" height="240" rx="120" fill="url(#bg)"/>
      <circle cx="120" cy="106" r="76" fill="rgba(255,255,255,0.16)"/>
      <circle cx="120" cy="96" r="40" fill="rgba(255,255,255,0.9)"/>
      <path d="M66 198c10-32 37-49 54-49h0c17 0 44 17 54 49" fill="rgba(255,255,255,0.86)"/>
      <path d="M86 109c0-19 15-34 34-34s34 15 34 34c0 4-1 8-2 12-4-6-12-10-18-10-6 0-10 2-14 6-4-4-8-6-14-6-6 0-14 4-18 10-1-4-2-8-2-12Z" fill="${palette[2]}"/>
      <path d="M92 97c6-17 20-27 28-27 8 0 22 10 28 27" fill="${palette[2]}"/>
      <circle cx="105" cy="104" r="5" fill="${palette[3]}"/>
      <circle cx="135" cy="104" r="5" fill="${palette[3]}"/>
      <path d="M103 122c8 6 26 6 34 0" fill="none" stroke="${palette[3]}" stroke-width="6" stroke-linecap="round"/>
      <circle cx="120" cy="120" r="116" fill="url(#glow)"/>
      <text x="120" y="198" text-anchor="middle" fill="rgba(255,255,255,0.92)" font-family="Inter, Segoe UI, sans-serif" font-size="28" font-weight="800" letter-spacing="1">${initials}</text>
    </svg>
  `;

  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function pickPalette(seed) {
  const palettes = [
    ['#6d28d9', '#4f46e5', '#f8fafc', '#1e1b4b'],
    ['#7c3aed', '#5b21b6', '#f5f3ff', '#2e1065'],
    ['#4f46e5', '#6366f1', '#eef2ff', '#1e1b4b'],
    ['#5b21b6', '#9333ea', '#faf5ff', '#2e1065'],
  ];

  const index = hashString(seed) % palettes.length;
  return palettes[index];
}

function hashString(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

function clampScore(score) {
  const value = Number(score);
  if (Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(SCORE_MAX, value));
}

function formatScore(score) {
  const value = clampScore(score);
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

function formatDate(date) {
  if (!date) return '-';
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function getRecommendationTone(recommendation) {
  switch (recommendation) {
    case 'Strong Hire':
      return { bg: 'var(--success-bg)', color: 'var(--success)', border: '#bbf7d0' };
    case 'Hire':
      return { bg: 'var(--primary-bg)', color: 'var(--primary)', border: '#ddd6fe' };
    case 'Hold':
      return { bg: 'var(--warning-bg)', color: 'var(--warning)', border: '#fde68a' };
    case 'Reject':
      return { bg: 'var(--danger-bg)', color: 'var(--danger)', border: '#fecaca' };
    default:
      return { bg: '#f3f4f6', color: 'var(--text-muted)', border: '#e5e7eb' };
  }
}

const s = {
  page: {
    minHeight: '100vh',
    background: 'linear-gradient(180deg, #fbfbff 0%, var(--bg) 100%)',
    padding: '14px 18px 18px',
  },
  shell: {
    maxWidth: 1720,
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  topbar: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.9fr) minmax(260px, 1fr)',
    gap: 10,
    alignItems: 'center',
  },
  topbarLeft: {
    minWidth: 0,
  },
  backLine: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexWrap: 'wrap',
    marginBottom: 4,
  },
  backBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    color: 'var(--primary)',
    background: 'rgba(91, 61, 245, 0.08)',
    borderRadius: 999,
    padding: '9px 14px',
    fontSize: 13,
    fontWeight: 600,
    flexShrink: 0,
    marginBottom: 8,
  },
  kicker: {
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: '0.12em',
    textTransform: 'uppercase',
    color: 'var(--primary)',
    marginBottom: 4,
  },
  summaryCard: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    boxShadow: 'var(--shadow)',
    padding: 12,
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.85fr) minmax(260px, 1fr)',
    gap: 10,
    alignItems: 'center',
  },
  summaryLeft: {
    minWidth: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 0,
  },
  summaryCopy: {
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    flex: 1,
  },
  summaryIdentity: {
    minWidth: 0,
    display: 'grid',
    gap: 6,
  },
  summaryTitleRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-start',
    gap: 10,
    flexWrap: 'wrap',
  },
  candidateName: {
    fontSize: 22,
    lineHeight: 1.08,
    fontWeight: 800,
    letterSpacing: '-0.03em',
    color: 'var(--text)',
    marginBottom: 4,
  },
  candidateRole: {
    fontSize: 14,
    fontWeight: 600,
    color: 'var(--primary)',
    margin: 0,
  },
  summaryMetaRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    flexWrap: 'wrap',
  },
  inlineMetaItem: {
    display: 'inline-flex',
    alignItems: 'baseline',
    gap: 4,
    whiteSpace: 'nowrap',
  },
  inlineMetaLabel: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    color: 'var(--text-muted)',
  },
  inlineMetaValue: {
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--text)',
  },
  chipRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  chipRowCompact: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
    justifyContent: 'flex-start',
  },
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 10px',
    borderRadius: 999,
    background: 'var(--primary-bg)',
    color: 'var(--text-body)',
    fontSize: 12,
    fontWeight: 600,
  },
  summaryScorePanel: {
    minWidth: 0,
    padding: 12,
    borderRadius: 16,
    border: '1px solid #d9f7e8',
    background: 'linear-gradient(180deg, rgba(236,253,245,0.95) 0%, rgba(248,255,251,0.96) 100%)',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    justifyContent: 'center',
  },
  summaryScoreHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  summaryScoreIcon: {
    width: 30,
    height: 30,
    borderRadius: 10,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(91, 61, 245, 0.12)',
    color: 'var(--primary)',
    flexShrink: 0,
  },
  scoreLabel: {
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
    color: 'var(--text-muted)',
  },
  summaryScoreRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 14,
    flexWrap: 'wrap',
  },
  summaryScoreValue: {
    fontSize: 36,
    lineHeight: 1,
    fontWeight: 800,
    color: 'var(--primary)',
    letterSpacing: '-0.05em',
  },
  scoreDenom: {
    fontSize: 14,
    fontWeight: 500,
    color: 'var(--text-muted)',
    marginLeft: 2,
  },
  summaryScoreHint: {
    fontSize: 12.5,
    lineHeight: 1.45,
    color: 'var(--text-muted)',
  },
  recommendation: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 999,
    border: '1px solid transparent',
    padding: '8px 14px',
    fontSize: 13,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },
  mainGrid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.85fr) minmax(300px, 1fr)',
    gap: 10,
    alignItems: 'start',
  },
  leftColumn: {
    minWidth: 0,
    display: 'grid',
    gap: 10,
  },
  rightColumn: {
    minWidth: 0,
    display: 'grid',
    gap: 10,
  },
  videoCard: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    boxShadow: 'var(--shadow)',
    display: 'flex',
    flexDirection: 'column',
    padding: 8,
    gap: 0,
  },
  videoFrame: {
    width: '100%',
    aspectRatio: '16 / 6.5',
    // height: 'clamp(180px, 22vh, 240px)',
    borderRadius: 16,
    overflow: 'hidden',
    background: '#0f172a',
    border: '1px solid rgba(15,23,42,0.08)',
  },
  video: {
    width: '100%',
    height: '100%',
    display: 'block',
    objectFit: 'cover',
    background: '#0f172a',
  },
  videoSkeleton: {
    width: '100%',
    height: '100%',
    borderRadius: 16,
  },
  placeholder: {
    width: '100%',
    height: '100%',
    background: 'linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: 18,
    textAlign: 'center',
  },
  placeholderIcon: {
    width: 60,
    height: 60,
    borderRadius: '50%',
    background: 'var(--surface)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    boxShadow: 'var(--shadow)',
  },
  placeholderTitle: {
    fontSize: 15,
    fontWeight: 700,
    color: 'var(--text)',
  },
  placeholderText: {
    maxWidth: 360,
    fontSize: 13.5,
    color: 'var(--text-muted)',
  },
  panel: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    boxShadow: 'var(--shadow)',
    padding: 10,
  },
  panelHead: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 8,
  },
  panelTitle: {
    fontSize: 18,
    fontWeight: 800,
    letterSpacing: '-0.03em',
    color: 'var(--text)',
    marginBottom: 4,
  },
  panelSub: {
    fontSize: 12.5,
    color: 'var(--text-muted)',
    lineHeight: 1.45,
  },
  panelActionWrap: {
    flexShrink: 0,
  },
  panelActionBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    border: '1px solid rgba(91,61,245,0.18)',
    background: 'rgba(91,61,245,0.08)',
    color: 'var(--primary)',
    borderRadius: 999,
    padding: '6px 12px',
    fontSize: 12,
    fontWeight: 700,
    whiteSpace: 'nowrap',
  },
  narrativePreview: {
    fontSize: 14.5,
    lineHeight: 1.7,
    color: 'var(--text-body)',
    display: '-webkit-box',
    WebkitLineClamp: 3,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  },
  scoreList: {
    display: 'grid',
    gap: 8,
  },
  scoreMetric: {
    display: 'grid',
    gap: 6,
  },
  scoreMetricTop: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 14,
  },
  scoreMetricLabel: {
    fontSize: 13.5,
    fontWeight: 700,
    color: 'var(--text-body)',
  },
  scoreMetricValue: {
    fontSize: 13,
    fontWeight: 700,
    color: 'var(--text)',
    whiteSpace: 'nowrap',
  },
  track: {
    height: 7,
    background: '#e5e7eb',
    borderRadius: 999,
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    borderRadius: 999,
    background: 'var(--primary)',
    transition: 'width .7s ease',
  },
  scoreMetricText: {
    fontSize: 12.5,
    lineHeight: 1.3,
    color: 'var(--text-muted)',
    marginTop: 0,
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
  },
  justification: {
    fontSize: 14.5,
    lineHeight: 1.7,
    color: 'var(--text-body)',
  },
  modalOverlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(15, 23, 42, 0.42)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 18,
    zIndex: 1000,
  },
  modal: {
    width: 'min(880px, 100%)',
    maxHeight: 'min(82vh, 860px)',
    overflow: 'auto',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    boxShadow: '0 24px 60px rgba(15, 23, 42, 0.28)',
    padding: 18,
  },
  modalHead: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 16,
    marginBottom: 14,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: 800,
    letterSpacing: '-0.03em',
    color: 'var(--text)',
  },
  modalClose: {
    border: '1px solid var(--border)',
    background: 'var(--surface)',
    color: 'var(--text-muted)',
    borderRadius: 999,
    width: 32,
    height: 32,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    flexShrink: 0,
  },
  modalBody: {
    fontSize: 14.5,
    lineHeight: 1.75,
    color: 'var(--text-body)',
    whiteSpace: 'pre-wrap',
  },
  summarySkeleton: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.85fr) minmax(260px, 1fr)',
    gap: 12,
    alignItems: 'center',
  },
  summaryCopySkeleton: {
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  summaryIdentitySkeleton: {
    display: 'grid',
    gap: 8,
  },
  summaryMetaRowSkeleton: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexWrap: 'wrap',
  },
  metaItemSkeleton: {
    width: 128,
    height: 18,
    borderRadius: 8,
  },
  chipRowSkeleton: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  chipSkeleton: {
    width: 120,
    height: 26,
    borderRadius: 999,
  },
  summaryScoreSkeleton: {
    minWidth: 0,
    padding: 12,
    borderRadius: 16,
    border: '1px solid #d9f7e8',
    background: 'linear-gradient(180deg, rgba(236,253,245,0.95) 0%, rgba(248,255,251,0.96) 100%)',
  },
  footer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    flexWrap: 'wrap',
    padding: '0 2px 0',
    color: 'var(--text-muted)',
    fontSize: 12.5,
  },
  footerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    flexWrap: 'wrap',
  },
  footerBrand: {
    width: 28,
    height: 28,
    borderRadius: 999,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'linear-gradient(135deg, var(--primary), var(--primary-secondary))',
    color: '#fff',
    fontSize: 12,
    fontWeight: 800,
    boxShadow: '0 10px 18px rgba(91, 61, 245, 0.18)',
  },
  footerRight: {
    color: 'var(--primary)',
    fontWeight: 600,
    fontSize: 12.5,
  },
};
