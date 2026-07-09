import { MOCK_CANDIDATES as MOCK_INTERVIEWS } from '../hooks/useInterview';

export const WORKFLOW_STAGES = [
  { value: 'all', label: 'All stages' },
  { value: 'candidates_searched', label: 'Candidates Searched' },
  { value: 'selected_by_client', label: 'Selected by Client/User' },
  { value: 'reached_out', label: 'Reached Out' },
  { value: 'responded', label: 'Responded' },
  { value: 'interview_scheduled', label: 'Interview Scheduled' },
  { value: 'interview_completed', label: 'Interview Completed' },
  { value: 'shortlisted', label: 'Shortlisted' },
  { value: 'rejected', label: 'Rejected' },
];

export const ADAM_JOBS = [
  {
    id: 'JOB-ADAM-1001',
    title: 'Senior Frontend Engineer',
    companyName: 'Northstar Payments',
    department: 'Engineering',
    employmentType: 'Full-time',
    location: 'Remote (US)',
    experienceRequired: '5+ years',
    status: 'Open',
    createdAt: '2024-07-01',
    source: 'adam',
  },
  {
    id: 'JOB-ADAM-1002',
    title: 'Backend Engineer',
    companyName: 'Atlas Commerce',
    department: 'Platform',
    employmentType: 'Full-time',
    location: 'Austin, TX',
    experienceRequired: '4+ years',
    status: 'In Review',
    createdAt: '2024-07-03',
    source: 'adam',
  },
  {
    id: 'JOB-ADAM-1003',
    title: 'Full Stack Engineer',
    companyName: 'Vertex Health',
    department: 'Product Engineering',
    employmentType: 'Contract',
    location: 'New York, NY',
    experienceRequired: '3+ years',
    status: 'Closed',
    createdAt: '2024-07-08',
    source: 'adam',
  },
  {
    id: 'JOB-DB-2001',
    title: 'Platform Engineer',
    companyName: 'Legacy Studio',
    department: 'Operations',
    employmentType: 'Full-time',
    location: 'Bengaluru, IN',
    experienceRequired: '6+ years',
    status: 'Draft',
    createdAt: '2024-06-22',
    source: 'dashboard',
  },
];

const interviewIndex = new Map(MOCK_INTERVIEWS.map((item) => [item.id, item]));

export const ADAM_CANDIDATES = [
  {
    id: 'CAND-ADAM-3001',
    name: 'Sarah Mitchell',
    email: 'sarah.mitchell@example.com',
    phone: '+1 (415) 555-0142',
    jobId: 'JOB-ADAM-1001',
    workflowStage: 'shortlisted',
    workflowStatus: 'Shortlisted',
    interviewStatus: 'Completed',
    overallInterviewScore: 8.2,
    interviewId: 'INT-2024-0042',
    resumeSummary:
      'Frontend engineer with strong React, TypeScript, and design system experience. Brings six years of product work across large-scale single-page applications.',
    personalInfo: {
      location: 'San Francisco, CA',
      experience: '6 years',
      currentRole: 'Senior Frontend Engineer',
    },
    communicationHistory: [
      { date: '2024-07-02', channel: 'Email', note: 'ADAM shared the candidate with the client for review.' },
      { date: '2024-07-03', channel: 'Portal', note: 'Client/User selected the candidate from AI recommendations.' },
      { date: '2024-07-04', channel: 'Email', note: 'Interview slot confirmed after outreach.' },
    ],
  },
  {
    id: 'CAND-ADAM-3002',
    name: 'James Okafor',
    email: 'james.okafor@example.com',
    phone: '+1 (512) 555-0108',
    jobId: 'JOB-ADAM-1002',
    workflowStage: 'interview_completed',
    workflowStatus: 'Interview Completed',
    interviewStatus: 'Completed',
    overallInterviewScore: 7.4,
    interviewId: 'INT-2024-0043',
    resumeSummary:
      'Backend engineer focused on API design, database tuning, and service reliability. Has experience shipping microservices in production environments.',
    personalInfo: {
      location: 'Austin, TX',
      experience: '4 years',
      currentRole: 'Backend Engineer',
    },
    communicationHistory: [
      { date: '2024-07-05', channel: 'Email', note: 'Outreach email sent after client selection.' },
      { date: '2024-07-06', channel: 'Email', note: 'Candidate replied and confirmed availability.' },
      { date: '2024-07-08', channel: 'Calendar', note: 'Interview completed and score recorded.' },
    ],
  },
  {
    id: 'CAND-ADAM-3003',
    name: 'Priya Nair',
    email: 'priya.nair@example.com',
    phone: '+1 (917) 555-0184',
    jobId: 'JOB-ADAM-1003',
    workflowStage: 'rejected',
    workflowStatus: 'Rejected',
    interviewStatus: 'Completed',
    overallInterviewScore: 6.1,
    interviewId: 'INT-2024-0044',
    resumeSummary:
      'Full stack engineer with broad exposure to frontend delivery and early backend ownership. Strong collaborator with room to grow into deeper system complexity.',
    personalInfo: {
      location: 'New York, NY',
      experience: '3 years',
      currentRole: 'Full Stack Engineer',
    },
    communicationHistory: [
      { date: '2024-07-09', channel: 'Portal', note: 'Client/User selected the candidate after AI screening.' },
      { date: '2024-07-10', channel: 'Email', note: 'Interview completed, but the hiring team chose not to advance.' },
    ],
  },
  {
    id: 'CAND-ADAM-3004',
    name: 'Omar Hassan',
    email: 'omar.hassan@example.com',
    phone: '+1 (646) 555-0199',
    jobId: 'JOB-ADAM-1001',
    workflowStage: 'responded',
    workflowStatus: 'Responded',
    interviewStatus: 'Scheduled',
    overallInterviewScore: null,
    interviewId: null,
    resumeSummary:
      'Product-focused frontend engineer with strong accessibility habits and responsive UI delivery. Interview booking is pending after candidate response.',
    personalInfo: {
      location: 'Chicago, IL',
      experience: '5 years',
      currentRole: 'Frontend Engineer',
    },
    communicationHistory: [
      { date: '2024-07-06', channel: 'Email', note: 'Reached out after client selection.' },
      { date: '2024-07-07', channel: 'Email', note: 'Candidate replied with interest and asked for available slots.' },
    ],
  },
];

const candidatesById = new Map(ADAM_CANDIDATES.map((item) => [item.id, item]));
const jobsById = new Map(ADAM_JOBS.map((item) => [item.id, item]));

export function getAdamJobs() {
  return ADAM_JOBS.filter((job) => job.source === 'adam');
}

export function getAdamJobById(jobId) {
  return jobsById.get(jobId) ?? null;
}

export function getAdamCandidates() {
  return ADAM_CANDIDATES.filter((candidate) => getAdamJobById(candidate.jobId)?.source === 'adam');
}

export function getAdamCandidateById(candidateId) {
  return candidatesById.get(candidateId) ?? null;
}

export function getInterviewById(interviewId) {
  if (!interviewId) return null;
  return interviewIndex.get(interviewId) ?? null;
}

