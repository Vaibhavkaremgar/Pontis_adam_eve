import { NavLink, useNavigate } from 'react-router-dom';
import { Briefcase, Users, Video, LogOut, LayoutDashboard } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const ownerLinks = [
  { to: '/jobs',       label: 'Jobs',        icon: Briefcase },
  { to: '/candidates', label: 'Candidates',  icon: Users },
  { to: '/interviews', label: 'Interviews',  icon: Video },
];

const userLinks = [
  { to: '/interviews', label: 'Interviews', icon: Video },
];

export default function Sidebar() {
  const { user, logout, isProductOwner } = useAuth();
  const navigate = useNavigate();
  const links = isProductOwner ? ownerLinks : userLinks;

  const handleLogout = () => { logout(); navigate('/login'); };

  return (
    <aside style={styles.sidebar}>
      <div style={styles.brand}>
        {/* <LayoutDashboard size={22} color="var(--primary)" /> */}
        <span style={styles.brandText}>Pontis</span>
      </div>

      <nav style={styles.nav}>
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.linkActive : {}) })}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      <div style={styles.footer}>
        <div style={styles.userInfo}>
          <div style={styles.avatar}>{user?.name?.[0] ?? 'U'}</div>
          <div>
            <div style={styles.userName}>{user?.name}</div>
            <div style={styles.userRole}>{isProductOwner ? 'Product Owner' : 'Client/User'}</div>
          </div>
        </div>
        <button onClick={handleLogout} style={styles.logoutBtn} title="Logout">
          <LogOut size={16} />
        </button>
      </div>
    </aside>
  );
}

const styles = {
  sidebar: {
    width: 'var(--sidebar-w)',
    minHeight: '100vh',
    background: 'var(--surface)',
    borderRight: '1px solid var(--border)',
    display: 'flex',
    flexDirection: 'column',
    position: 'fixed',
    top: 0, left: 0, bottom: 0,
    zIndex: 100,
  },
  brand: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '24px 24px 20px',
    borderBottom: '1px solid var(--border)',
  },
  brandText: { fontWeight: 700, fontSize: 18, color: 'var(--text)' },
  nav: { flex: 1, padding: '16px 14px', display: 'flex', flexDirection: 'column', gap: 8 },
  link: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '12px 14px', borderRadius: 12,
    color: 'var(--text-muted)', fontWeight: 500, fontSize: 15,
    transition: 'all .15s',
  },
  linkActive: {
    background: 'var(--primary-bg)', color: 'var(--primary)', fontWeight: 600,
  },
  footer: {
    padding: '16px 18px 20px',
    borderTop: '1px solid var(--border)',
    display: 'flex', alignItems: 'center', gap: 8,
  },
  userInfo: { flex: 1, display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' },
  avatar: {
    width: 38, height: 38, borderRadius: '50%',
    background: 'var(--primary-bg)', color: 'var(--primary)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontWeight: 700, fontSize: 14, flexShrink: 0,
  },
  userName: { fontSize: 14, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  userRole: { fontSize: 12, color: 'var(--text-muted)' },
  logoutBtn: {
    padding: 8, borderRadius: 10, color: 'var(--text-muted)',
    display: 'flex', alignItems: 'center',
    transition: 'color .15s',
  },
};
