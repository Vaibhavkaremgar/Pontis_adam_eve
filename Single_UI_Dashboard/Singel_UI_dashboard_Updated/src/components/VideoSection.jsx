import { VideoOff } from 'lucide-react';

export default function VideoSection({ videoUrl, loading, compact = false }) {
  if (loading) return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }}>
      <div className="skeleton" style={{ width: 180, height: compact ? 16 : 18, marginBottom: 14 }} />
      <div className="skeleton" style={{ width: '100%', aspectRatio: '16/9', borderRadius: 12 }} />
    </div>
  );

  return (
    <div style={{ ...s.card, ...(compact ? s.cardCompact : null) }} className="fade-in">
      <h3 style={{ ...s.title, ...(compact ? s.titleCompact : null) }}>Interview Recording</h3>
      <div style={s.mediaWrap}>
        {videoUrl ? (
          <video controls style={s.video} src={videoUrl} />
        ) : (
          <div style={s.placeholder}>
            <div style={s.iconWrap}>
              <VideoOff size={36} color="var(--text-light)" />
            </div>
            <p style={s.ph1}>No Recording Available</p>
            <p style={s.ph2}>The interview recording has not been uploaded yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}

const s = {
  card: {
    background: 'var(--surface)', borderRadius: 18,
    boxShadow: 'var(--shadow)', border: '1px solid var(--border)', padding: '16px',
    display: 'flex',
    flexDirection: 'column',
    minHeight: 0,
    height: '100%',
  },
  cardCompact: { padding: '12px' },
  title: { fontSize: 24, fontWeight: 700, color: 'var(--text)', marginBottom: 12 },
  titleCompact: { fontSize: 17, marginBottom: 9 },
  mediaWrap: {
    width: '100%',
    aspectRatio: '16/9',
    borderRadius: 18,
    overflow: 'hidden',
    background: '#000',
    minHeight: 0,
    flex: 1,
  },
  video: {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    background: '#000',
    display: 'block',
  },
  placeholder: {
    width: '100%', height: '100%', background: '#f8fafc',
    border: '2px dashed var(--border)',
    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8,
  },
  iconWrap: {
    width: 68, height: 68, borderRadius: '50%', background: 'var(--primary-bg)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 4,
  },
  ph1: { fontSize: 16, fontWeight: 600, color: 'var(--text)' },
  ph2: { fontSize: 16, color: 'var(--text-muted)' },
};
