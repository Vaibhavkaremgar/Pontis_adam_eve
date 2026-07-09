import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
// import { LayoutDashboard } from 'lucide-react';

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const user = login(email, password);
      navigate(user.role === 'product_owner' ? '/jobs' : '/interviews');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.brand}>
          {/* <LayoutDashboard size={28} color="var(--primary)" /> */}
          <span style={styles.brandText}>Pontis</span>
        </div>
        {/* <h1 style={styles.title}>Welcome back</h1> */}
        <p style={styles.brandText}>Sign in to your account</p>

        <form onSubmit={handleSubmit} style={styles.form}>
          <label style={styles.label}>Email</label>
          <input
            style={styles.input}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            autoFocus
          />
          <label style={styles.label}>Password</label>
          <input
            style={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            required
          />
          {error && <p style={styles.error}>{error}</p>}
          <button style={{ ...styles.btn, opacity: loading ? 0.7 : 1 }} type="submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        <div style={styles.hints}>
          <p style={styles.hintTitle}>Demo accounts</p>
          <p style={styles.hint}><b>Product Owner:</b> owner@demo.com / demo123</p>
          <p style={styles.hint}><b>User:</b> user@demo.com / demo123</p>
        </div>
      </div>
    </div>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'var(--bg)',
    padding: 32,
  },
  card: {
    background: 'var(--surface)',
    borderRadius: 20,
    boxShadow: 'var(--shadow-md)',
    border: '1px solid var(--border)',
    padding: '40px 36px',
    width: '100%',
    maxWidth: 440,
  },
  brand: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 },
  brandText: { fontSize: 22, fontWeight: 700, color: 'var(--text)' },
  title: { fontSize: 40, fontWeight: 700, color: 'var(--text)', marginBottom: 6 },
  sub: { fontSize: 18, fontWeight: 400, color: 'var(--text-muted)', marginBottom: 28 },
  form: { display: 'flex', flexDirection: 'column' },
  label: { fontSize: 16, fontWeight: 500, color: 'var(--text)', marginBottom: 8 },
  input: {
    border: '1px solid var(--border)',
    borderRadius: 12,
    padding: '12px 14px',
    fontSize: 16,
    marginBottom: 18,
    outline: 'none',
    color: 'var(--text)',
    transition: 'border-color .15s',
  },
  error: { fontSize: 14, color: 'var(--danger)', marginBottom: 12 },
  btn: {
    background: 'var(--primary)',
    color: '#fff',
    borderRadius: 12,
    padding: '12px',
    fontSize: 16,
    fontWeight: 600,
    marginTop: 4,
    transition: 'background .15s',
  },
  hints: {
    marginTop: 24,
    padding: 16,
    background: 'var(--primary-bg)',
    borderRadius: 12,
    border: '1px solid #ddd6fe',
  },
  hintTitle: { fontSize: 14, fontWeight: 600, color: 'var(--text)', marginBottom: 6 },
  hint: { fontSize: 14, color: 'var(--text-muted)', marginBottom: 2 },
};
