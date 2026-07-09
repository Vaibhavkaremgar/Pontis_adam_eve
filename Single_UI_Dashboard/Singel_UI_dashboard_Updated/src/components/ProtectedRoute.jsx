import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

// requireOwner: if true, only product_owner can access; users get redirected
export default function ProtectedRoute({ children, requireOwner = false }) {
  const { user, isProductOwner } = useAuth();

  if (!user) return <Navigate to="/login" replace />;
  if (requireOwner && !isProductOwner) return <Navigate to="/interviews" replace />;

  return children;
}
