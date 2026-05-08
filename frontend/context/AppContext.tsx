"use client";

/**
 * What this file does:
 * Provides global frontend orchestration state across all intake steps.
 *
 * What API it connects to:
 * Stores request/response state for /auth/me, /hiring/create, /candidates,
 * /voice/refine, /outreach, and /interviews.
 *
 * How it fits in the pipeline:
 * Frontend keeps only orchestration/session data (forms, ids, results),
 * restores the recruiter profile from cookies on app load, and never stores tokens in localStorage.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
  type SetStateAction
} from "react";
import { usePathname, useRouter } from "next/navigation";

import {
  clearSession,
  getStoredUser,
  storeSession,
  storePipelineState,
  getStoredPipelineState,
  clearPipelineState,
} from "@/lib/session";
import { getCurrentUser, logout as logoutApi } from "@/lib/api/auth";
import type { Candidate, Company, Job, User } from "@/types";

type CallStatus = "idle" | "connecting" | "listening" | "speaking" | "processing" | "completed" | "error";

type AppContextValue = {
  user: User | null;
  isSessionReady: boolean;
  company: Company;
  job: Job;
  jobId: string;
  candidates: Candidate[];
  voiceNotes: string[];
  isRefined: boolean;
  callStatus: CallStatus;
  setUser: (data: User | null) => void;
  setCompany: (data: Company) => void;
  setJob: (data: Job) => void;
  setJobId: (id: string) => void;
  setCandidates: (data: Candidate[]) => void;
  setVoiceNotes: (notes: SetStateAction<string[]>) => void;
  setIsRefined: (value: boolean) => void;
  setCallStatus: (value: CallStatus) => void;
  logout: () => void;
};

const initialCompany: Company = {
  name: "",
  website: "",
  description: "",
  industry: "",
  atsProvider: "",
  atsConnected: false
};

const initialJob: Job = {
  title: "",
  description: "",
  location: "",
  compensation: "",
  workAuthorization: "required",
  remotePolicy: "hybrid",
  experienceRequired: "",
  vettingMode: "volume",
  autoExportToAts: false
};

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const [user, setUserState] = useState<User | null>(null);
  const [isSessionReady, setIsSessionReady] = useState(false);
  const [company, setCompanyState] = useState<Company>(initialCompany);
  const [job, setJobState] = useState<Job>(initialJob);
  const [jobId, setJobIdState] = useState("");
  const [candidates, setCandidatesState] = useState<Candidate[]>([]);
  const [voiceNotes, setVoiceNotesState] = useState<string[]>([]);
  const [isRefined, setIsRefinedState] = useState(false);

  // Prepared voice call state.
  const [callStatus, setCallStatusState] = useState<CallStatus>("idle");

  const setUser = useCallback((data: User | null) => setUserState(data), []);
  const setCompany = useCallback((data: Company) => setCompanyState(data), []);
  const setJob = useCallback((data: Job) => setJobState(data), []);
  const setJobId = useCallback((id: string) => setJobIdState(id), []);
  const setCandidates = useCallback((data: Candidate[]) => setCandidatesState(data), []);
  const setVoiceNotes = useCallback((notes: SetStateAction<string[]>) => setVoiceNotesState(notes), []);
  const setIsRefined = useCallback((value: boolean) => setIsRefinedState(value), []);
  const setCallStatus = useCallback((value: CallStatus) => setCallStatusState(value), []);

  const logout = useCallback(() => {
    // Fully reset session and flow state, then return recruiter to login screen.
    void logoutApi().catch(() => undefined);
    clearSession();
    clearPipelineState();
    setUserState(null);
    setCompanyState(initialCompany);
    setJobState(initialJob);
    setJobIdState("");
    setCandidatesState([]);
    setVoiceNotesState([]);
    setIsRefinedState(false);
    setCallStatusState("idle");

    if (pathname !== "/login") {
      router.replace("/login");
    }
  }, [pathname, router]);

  // On app load, restore recruiter profile from cookies + pipeline state from sessionStorage.
  useEffect(() => {
    const storedUser = getStoredUser();
    const pipeline = getStoredPipelineState();
    let cancelled = false;

    const restore = async () => {
      if (storedUser) {
        setUserState(storedUser);
      }
      try {
        const currentUser = await getCurrentUser();
        if (!cancelled && currentUser.success && currentUser.data?.user) {
          setUserState(currentUser.data.user);
        }
      } catch {
        // Ignore bootstrap auth failures and continue the flow shell.
      }
      if (cancelled) return;
      if (pipeline.jobId) setJobIdState(pipeline.jobId);
      if (pipeline.job) setJobState(pipeline.job);
      if (pipeline.company) setCompanyState(pipeline.company);
      if (pipeline.isRefined) setIsRefinedState(pipeline.isRefined);
      setIsSessionReady(true);
    };

    void restore();

    return () => {
      cancelled = true;
    };
  }, []);

  // Persist or clear the cached user profile whenever auth state changes.
  useEffect(() => {
    if (user) {
      storeSession(user);
      return;
    }
    clearSession();
  }, [user]);

  // Persist pipeline state to sessionStorage whenever it changes.
  useEffect(() => {
    storePipelineState({ jobId, job, company, isRefined });
  }, [jobId, job, company, isRefined]);

  // Global 401 handling: when API client emits unauthorized event, force logout.
  useEffect(() => {
    const handleUnauthorized = () => logout();

    if (typeof window !== "undefined") {
      window.addEventListener("auth:unauthorized", handleUnauthorized);
    }

    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("auth:unauthorized", handleUnauthorized);
      }
    };
  }, [logout]);

  const value = useMemo(
    () => ({
      user,
      isSessionReady,
      company,
      job,
      jobId,
      candidates,
      voiceNotes,
      isRefined,
      callStatus,
      setUser,
      setCompany,
      setJob,
      setJobId,
      setCandidates,
      setVoiceNotes,
      setIsRefined,
      setCallStatus,
      logout
    }),
    [
      user,
      isSessionReady,
      company,
      job,
      jobId,
      candidates,
      voiceNotes,
      isRefined,
      callStatus,
      setUser,
      setCompany,
      setJob,
      setJobId,
      setCandidates,
      setVoiceNotes,
      setIsRefined,
      setCallStatus,
      logout
    ]
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext() {
  const context = useContext(AppContext);

  if (!context) {
    throw new Error("useAppContext must be used within an AppProvider");
  }

  return context;
}

export { initialCompany, initialJob };

