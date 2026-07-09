import { createContext, useContext, useState } from 'react';

const AuthContext = createContext(null);

const MOCK_USERS = [
  { id: 1, name: 'Alice Johnson', email: 'owner@demo.com', password: 'demo123', role: 'product_owner' },
  { id: 2, name: 'Bob Smith',     email: 'user@demo.com',  password: 'demo123', role: 'user' },
];

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('auth_user')); } catch { return null; }
  });

  const login = (email, password) => {
    const found = MOCK_USERS.find(u => u.email === email && u.password === password);
    if (!found) throw new Error('Invalid credentials');
    const { password: _p, ...safe } = found;
    sessionStorage.setItem('auth_user', JSON.stringify(safe));
    setUser(safe);
    return safe;
  };

  const logout = () => {
    sessionStorage.removeItem('auth_user');
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, isProductOwner: user?.role === 'product_owner' }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
