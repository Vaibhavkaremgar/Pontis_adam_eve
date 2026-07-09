import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { useAuth } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';
import LoginPage from './pages/LoginPage';
import InterviewsPage       from './pages/InterviewsPage';
import InterviewReportPage  from './pages/InterviewReportPage';
import JobsPage from './pages/JobsPage';
import CandidatesPage from './pages/CandidatesPage';
import CandidateDetailsPage from './pages/CandidateDetailsPage';
import './index.css';
import './App.css';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<AppRedirect />} />

          <Route
            path="/interviews/:id"
            element={
              <ProtectedRoute>
                <InterviewReportPage />
              </ProtectedRoute>
            }
          />

          <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
            {/* Product Owner only */}
            <Route path="/jobs"       element={<ProtectedRoute requireOwner><JobsPage /></ProtectedRoute>} />
            <Route path="/candidates" element={<ProtectedRoute requireOwner><CandidatesPage /></ProtectedRoute>} />
            <Route path="/candidates/:id" element={<ProtectedRoute requireOwner><CandidateDetailsPage /></ProtectedRoute>} />

            {/* Both roles */}
            <Route path="/interviews" element={<InterviewsPage />} />
          </Route>

          {/* Default redirect */}
          <Route path="*" element={<AppRedirect />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

function AppRedirect() {
  const { user, isProductOwner } = useAuth();

  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={isProductOwner ? '/jobs' : '/interviews'} replace />;
}
