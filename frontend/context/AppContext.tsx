"use client";

/**
 * What this file does:
 * Provides global frontend orchestration state across all intake steps.
 *
 * What API it connects to:
 * Stores request/response state for /auth/login, /hiring/create, /candidates,
 * /voice/refine, /outreach, and /interviews.
 *
 * How it fits in the pipeline:
 * Frontend keeps only orchestration/session data (user token, forms, ids, results),
 * restores session from localStorage on app load, and never stores embeddings or AI logic.
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

import { clearSession, getStoredToken, getStoredUser, storeSession } from "@/lib/session";
import type { Candidate, Company, Job, User } from "@/types";

type CallStatus = "idle" | "connecting" | "listening" | "speaking" | "processing" | "completed" | "error";

type AppContextValue = {
  user: User | null;
  token: string;
  isSessionReady: boolean;
  company: Company;
  job: Job;
  jobId: string;
  candidates: Candidate[];
  voiceNotes: string[];
  isRefined: boolean;
  callStatus: CallStatus;
  transcript: string;
  setUser: (data: User | null) => void;
  setToken: (token: string) => void;
  setCompany: (data: Company) => void;
  setJob: (data: Job) => void;
  setJobId: (id: string) => void;
  setCandidates: (data: Candidate[]) => void;
  setVoiceNotes: (notes: SetStateAction<string[]>) => void;
  setIsRefined: (value: boolean) => void;
  setCallStatus: (value: CallStatus) => void;
  setTranscript: (value: string) => void;
  logout: () => void;
};

const initialCompany: Company = {
  name: "",
  website: "",
  description: ""
};

const initialJob: Job = {
  title: "",
  description: "",
  location: "",
  compensation: "",
  workAuthorization: "required"
};

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const [user, setUserState] = useState<User | null>(null);
  const [token, setTokenState] = useState("");
  const [isSessionReady, setIsSessionReady] = useState(false);
  const [company, setCompanyState] = useState<Company>(initialCompany);
  const [job, setJobState] = useState<Job>(initialJob);
  const [jobId, setJobIdState] = useState("");
  const [candidates, setCandidatesState] = useState<Candidate[]>([]);
  const [voiceNotes, setVoiceNotesState] = useState<string[]>([]);
  const [isRefined, setIsRefinedState] = useState(false);

  // Prepared voice call state for future realtime voice integration.
  const [callStatus, setCallStatusState] = useState<CallStatus>("idle");

  // Prepared transcript state for future voice-to-text streaming output.
  const [transcript, setTranscriptState] = useState("");

  const setUser = useCallback((data: User | null) => setUserState(data), []);
  const setToken = useCallback((nextToken: string) => setTokenState(nextToken), []);
  const setCompany = useCallback((data: Company) => setCompanyState(data), []);
  const setJob = useCallback((data: Job) => setJobState(data), []);
  const setJobId = useCallback((id: string) => setJobIdState(id), []);
  const setCandidates = useCallback((data: Candidate[]) => setCandidatesState(data), []);
  const setVoiceNotes = useCallback((notes: SetStateAction<string[]>) => setVoiceNotesState(notes), []);
  const setIsRefined = useCallback((value: boolean) => setIsRefinedState(value), []);
  const setCallStatus = useCallback((value: CallStatus) => setCallStatusState(value), []);
  const setTranscript = useCallback((value: string) => setTranscriptState(value), []);

  const logout = useCallback(() => {
    // Fully reset session and flow state, then return recruiter to login screen.
    clearSession();
    setUserState(null);
    setTokenState("");
    setCompanyState(initialCompany);
    setJobState(initialJob);
    setJobIdState("");
    setCandidatesState([]);
    setVoiceNotesState([]);
    setIsRefinedState(false);
    setCallStatusState("idle");
    setTranscriptState("");

    if (pathname !== "/login") {
      router.replace("/login");
    }
  }, [pathname, router]);

  // On app load, restore session from localStorage if token is present.
  useEffect(() => {
    const storedToken = getStoredToken();
    const storedUser = getStoredUser();
    let cancelled = false;

    queueMicrotask(() => {
      if (cancelled) return;
      if (storedToken && storedUser) {
        setTokenState(storedToken);
        setUserState(storedUser);
      }
      setIsSessionReady(true);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  // Persist or clear session whenever user/token changes.
  useEffect(() => {
    if (user && token) {
      storeSession(token, user);
      return;
    }

    clearSession();
  }, [token, user]);

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
      token,
      isSessionReady,
      company,
      job,
      jobId,
      candidates,
      voiceNotes,
      isRefined,
      callStatus,
      transcript,
      setUser,
      setToken,
      setCompany,
      setJob,
      setJobId,
      setCandidates,
      setVoiceNotes,
      setIsRefined,
      setCallStatus,
      setTranscript,
      logout
    }),
    [
      user,
      token,
      isSessionReady,
      company,
      job,
      jobId,
      candidates,
      voiceNotes,
      isRefined,
      callStatus,
      transcript,
      setUser,
      setToken,
      setCompany,
      setJob,
      setJobId,
      setCandidates,
      setVoiceNotes,
      setIsRefined,
      setCallStatus,
      setTranscript,
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

