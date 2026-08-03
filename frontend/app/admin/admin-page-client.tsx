"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Building2, LayoutDashboard, LogOut, Pencil, Plus, Power, Search, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { useAppContext } from "@/context/AppContext";
import { isSuperAdminRole, toAdminRoleValue } from "@/lib/roles";
import {
  createAgency,
  createUser,
  deactivateAgency,
  getAdminAgencies,
  getAdminDashboard,
  getAdminUsers,
  getAllAgencies,
  reactivateAgency,
  updateAgency,
  updateUser,
  type AdminDashboard,
  type AgencyRecord,
  type UserRecord,
} from "@/lib/api/admin";

type AdminSection = "dashboard" | "agencies" | "users";

type AdminPageProps = {
  view: AdminSection;
};

type ConfirmState = {
  title: string;
  description: string;
  confirmLabel: string;
  action: () => Promise<void> | void;
};

const PAGE_SIZE = 10;
const ROLE_OPTIONS = [
  { value: "AGENCY_USER", label: "Agency User" },
  { value: "SUPER_ADMIN", label: "Super Admin" },
];

function SectionNav({ current }: { current: AdminSection }) {
  const router = useRouter();
  const tabs: Array<{ label: string; value: AdminSection; icon: ReactNode; href: string }> = [
    { label: "Dashboard", value: "dashboard", href: "/admin", icon: <LayoutDashboard className="h-4 w-4" /> },
    { label: "Agencies", value: "agencies", href: "/admin/agencies", icon: <Building2 className="h-4 w-4" /> },
    { label: "Users", value: "users", href: "/admin/users", icon: <Users className="h-4 w-4" /> },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          onClick={() => router.push(tab.href)}
          className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition ${
            current === tab.value
              ? "border-[#14532D] bg-[#14532D] text-white"
              : "border-[rgba(120,100,80,0.15)] bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900"
          }`}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function PageShell({
  title,
  description,
  current,
  onLogout,
  children,
}: {
  title: string;
  description: string;
  current: AdminSection;
  onLogout: () => void;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(20,83,45,0.08),_transparent_30%),linear-gradient(180deg,_#f8faf6_0%,_#fffdfa_100%)]">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6">
        <div className="rounded-[28px] border border-[rgba(120,100,80,0.1)] bg-white/80 p-6 shadow-sm backdrop-blur">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="space-y-2">
                <div className="inline-flex items-center gap-3 rounded-full border border-[rgba(20,83,45,0.12)] bg-[#F5FAF4] px-3 py-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#14532D] text-sm font-semibold text-white">
                    AS
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#14532D]">Adam Super Admin</p>
                    <p className="text-sm text-slate-500">Signed in as super admin</p>
                  </div>
                </div>
                <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">{title}</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{description}</p>
              </div>
              <button
                type="button"
                onClick={onLogout}
                className="inline-flex items-center gap-2 self-start rounded-full border border-[rgba(20,83,45,0.12)] bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-[#14532D] hover:text-[#14532D] lg:self-auto"
              >
                <LogOut className="h-4 w-4" />
                Log out
              </button>
            </div>
            <div className="flex justify-start">
              <SectionNav current={current} />
            </div>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}

function PaginationBar({
  page,
  totalPages,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3 border-t border-slate-100 px-6 py-4 text-sm text-slate-600 sm:flex-row sm:items-center sm:justify-between">
      <p>
        Showing page {page} of {totalPages || 1} across {total} records
      </p>
      <div className="flex items-center gap-2">
        <select
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          {[10, 20, 50].map((size) => (
            <option key={size} value={size}>
              {size} / page
            </option>
          ))}
        </select>
        <Button variant="outline" size="sm" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={page <= 1}>
          Previous
        </Button>
        <Button variant="outline" size="sm" onClick={() => onPageChange(Math.min(totalPages || 1, page + 1))} disabled={page >= totalPages}>
          Next
        </Button>
      </div>
    </div>
  );
}

function ConfirmModal({
  confirm,
  onClose,
}: {
  confirm: ConfirmState | null;
  onClose: () => void;
}) {
  if (!confirm) return null;

  return (
    <Modal open={Boolean(confirm)} onOpenChange={(open) => !open && onClose()} title={confirm.title} description={confirm.description}>
      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={async () => await confirm.action()}>{confirm.confirmLabel}</Button>
      </div>
    </Modal>
  );
}

export default function AdminPageClient({ view }: AdminPageProps) {
  const router = useRouter();
  const { user, isSessionReady, logout } = useAppContext();

  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [agencyRows, setAgencyRows] = useState<AgencyRecord[]>([]);
  const [agencyPagination, setAgencyPagination] = useState({ page: 1, pageSize: PAGE_SIZE, total: 0, totalPages: 0 });
  const [userRows, setUserRows] = useState<UserRecord[]>([]);
  const [agencyOptions, setAgencyOptions] = useState<AgencyRecord[]>([]);
  const [userPagination, setUserPagination] = useState({ page: 1, pageSize: PAGE_SIZE, total: 0, totalPages: 0 });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [agencySearch, setAgencySearch] = useState("");
  const [agencyStatus, setAgencyStatus] = useState("");
  const [agencyPage, setAgencyPage] = useState(1);
  const [agencyPageSize, setAgencyPageSize] = useState(PAGE_SIZE);

  const [userSearch, setUserSearch] = useState("");
  const [userAgencyFilter, setUserAgencyFilter] = useState("");
  const [userRoleFilter, setUserRoleFilter] = useState("");
  const [userStatusFilter, setUserStatusFilter] = useState("");
  const [userPage, setUserPage] = useState(1);
  const [userPageSize, setUserPageSize] = useState(PAGE_SIZE);

  const [agencyModalOpen, setAgencyModalOpen] = useState(false);
  const [agencyModalMode, setAgencyModalMode] = useState<"create" | "edit">("create");
  const [selectedAgency, setSelectedAgency] = useState<AgencyRecord | null>(null);
  const [agencyName, setAgencyName] = useState("");

  const [userModalOpen, setUserModalOpen] = useState(false);
  const [userModalMode, setUserModalMode] = useState<"create" | "edit">("create");
  const [selectedUser, setSelectedUser] = useState<UserRecord | null>(null);
  const [userName, setUserName] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [userAgencyId, setUserAgencyId] = useState("");
  const [userRole, setUserRole] = useState("AGENCY_USER");
  const [userIsActive, setUserIsActive] = useState(true);

  const [confirm, setConfirm] = useState<ConfirmState | null>(null);

  const isSuperAdmin = isSuperAdminRole(user?.role);

  useEffect(() => {
    if (!isSessionReady) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    if (!isSuperAdmin) {
      router.replace("/workspace");
    }
  }, [isSessionReady, isSuperAdmin, router, user]);

  const loadDashboard = async () => {
    const [summary, agencies, users] = await Promise.all([
      getAdminDashboard(),
      getAdminAgencies({ page: 1, pageSize: 5 }),
      getAdminUsers({ page: 1, pageSize: 5 }),
    ]);
    if (summary.success && summary.data) setDashboard(summary.data);
    if (agencies.success && agencies.data) {
      setAgencyRows(agencies.data.items || []);
      setAgencyPagination(agencies.data.pagination);
    }
    if (users.success && users.data) {
      setUserRows(users.data.items || []);
      setUserPagination(users.data.pagination);
    }
  };

  const loadAgencies = async () => {
    setError("");
    const result = await getAdminAgencies({
      search: agencySearch,
      status: agencyStatus,
      page: agencyPage,
      pageSize: agencyPageSize,
    });
    if (result.success && result.data) {
      setAgencyRows(result.data.items || []);
      setAgencyPagination(result.data.pagination);
    } else {
      setError(result.error || "Unable to load agencies.");
    }
  };

  const loadUsers = async () => {
    setError("");
    const [result, agencies] = await Promise.all([
      getAdminUsers({
        search: userSearch,
        agencyId: userAgencyFilter,
        role: userRoleFilter,
        status: userStatusFilter,
        page: userPage,
        pageSize: userPageSize,
      }),
      getAllAgencies(),
    ]);
    if (result.success && result.data) {
      setUserRows(result.data.items || []);
      setUserPagination(result.data.pagination);
    } else {
      setError(result.error || "Unable to load users.");
    }
    if (agencies.success && agencies.data) {
      setAgencyOptions(agencies.data);
    }
  };

  useEffect(() => {
    if (!isSuperAdmin) return;
    let cancelled = false;
    const run = async () => {
      setError("");
      const [summary, agencies, users] = await Promise.all([
        getAdminDashboard(),
        getAdminAgencies({ page: 1, pageSize: 5 }),
        getAdminUsers({ page: 1, pageSize: 5 }),
      ]);
      if (cancelled) return;
      if (summary.success && summary.data) setDashboard(summary.data);
      if (agencies.success && agencies.data) {
        setAgencyRows(agencies.data.items || []);
        setAgencyPagination(agencies.data.pagination);
      }
      if (users.success && users.data) {
        setUserRows(users.data.items || []);
        setUserPagination(users.data.pagination);
      }
    };
    if (view === "dashboard") {
      void run();
    }
    return () => {
      cancelled = true;
    };
  }, [isSuperAdmin, view]);

  useEffect(() => {
    if (!isSuperAdmin || view !== "agencies") return;
    let cancelled = false;
    const run = async () => {
      setError("");
      const result = await getAdminAgencies({
        search: agencySearch,
        status: agencyStatus,
        page: agencyPage,
        pageSize: agencyPageSize,
      });
      if (cancelled) return;
      if (result.success && result.data) {
        setAgencyRows(result.data.items || []);
        setAgencyPagination(result.data.pagination);
      } else {
        setError(result.error || "Unable to load agencies.");
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [agencyPage, agencyPageSize, agencySearch, agencyStatus, isSuperAdmin, view]);

  useEffect(() => {
    if (!isSuperAdmin || view !== "users") return;
    let cancelled = false;
    const run = async () => {
      setError("");
      const [result, agencies] = await Promise.all([
        getAdminUsers({
          search: userSearch,
          agencyId: userAgencyFilter,
          role: userRoleFilter,
          status: userStatusFilter,
          page: userPage,
          pageSize: userPageSize,
        }),
        getAllAgencies(),
      ]);
      if (cancelled) return;
      if (result.success && result.data) {
        setUserRows(result.data.items || []);
        setUserPagination(result.data.pagination);
      } else {
        setError(result.error || "Unable to load users.");
      }
      if (agencies.success && agencies.data) {
        setAgencyOptions(agencies.data);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [isSuperAdmin, userAgencyFilter, userPage, userPageSize, userRoleFilter, userSearch, userStatusFilter, view]);

  const refreshCurrentView = async () => {
    if (view === "dashboard") {
      await loadDashboard();
    } else if (view === "agencies") {
      await loadAgencies();
    } else {
      await loadUsers();
    }
  };

  const openCreateAgency = () => {
    setSelectedAgency(null);
    setAgencyName("");
    setAgencyModalMode("create");
    setAgencyModalOpen(true);
  };

  const openEditAgency = (agency: AgencyRecord) => {
    setSelectedAgency(agency);
    setAgencyName(agency.name || "");
    setAgencyModalMode("edit");
    setAgencyModalOpen(true);
  };

  const submitAgency = async () => {
    const value = agencyName.trim();
    if (!value) {
      setError("Agency name is required.");
      return;
    }
    const result = agencyModalMode === "create"
      ? await createAgency({ name: value })
      : await updateAgency(String(selectedAgency?.id || ""), { name: value });
    if (!result.success || !result.data) {
      setError(result.error || "Unable to save agency.");
      return;
    }
    setMessage(agencyModalMode === "create" ? "Agency created." : "Agency updated.");
    setAgencyModalOpen(false);
    await refreshCurrentView();
  };

  const openCreateUser = () => {
    setSelectedUser(null);
    setUserName("");
    setUserEmail("");
    setUserAgencyId(agencyOptions[0]?.id || "");
    setUserRole("AGENCY_USER");
    setUserIsActive(true);
    setUserModalMode("create");
    setUserModalOpen(true);
  };

  const openEditUser = (userRow: UserRecord) => {
    setSelectedUser(userRow);
    setUserName(userRow.name || "");
    setUserEmail(userRow.email || "");
    setUserAgencyId(userRow.agencyId || "");
    setUserRole(toAdminRoleValue(userRow.role));
    setUserIsActive(userRow.status !== "Inactive");
    setUserModalMode("edit");
    setUserModalOpen(true);
  };

  const submitUser = async () => {
    const email = userEmail.trim();
    const name = userName.trim();
    if (!email || !userAgencyId) {
      setError("Agency and email are required.");
      return;
    }
    const payload = {
      agencyId: userAgencyId,
      name,
      email,
      role: userRole,
      isActive: userIsActive,
    };
    const result = userModalMode === "create"
      ? await createUser(payload)
      : await updateUser(String(selectedUser?.id || ""), payload);
    if (!result.success || !result.data) {
      setError(result.error || "Unable to save user.");
      return;
    }
    setMessage(userModalMode === "create" ? "User created." : "User updated.");
    setUserModalOpen(false);
    await refreshCurrentView();
  };

  const agencyPageItems = useMemo(() => agencyRows, [agencyRows]);
  const userGroups = useMemo(() => {
    const groups = new Map<string, UserRecord[]>();
    for (const item of userRows) {
      const key = item.agencyName || "Unassigned";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)?.push(item);
    }
    return Array.from(groups.entries()).map(([name, items]) => ({ name, items }));
  }, [userRows]);

  if (!isSuperAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-600">
        Loading admin access...
      </div>
    );
  }

  return (
    <PageShell
      title={view === "dashboard" ? "Dashboard" : view === "agencies" ? "Agencies" : "Users"}
      description={
        view === "dashboard"
          ? "Monitor agencies, users, jobs, and candidates from a single super-admin control surface."
          : view === "agencies"
            ? "Create, edit, deactivate, and reactivate agencies across the Adam workspace."
            : "Manage Adam users by agency, role, and status."
      }
      current={view}
      onLogout={logout}
    >
      {(message || error) && (
        <Card className={error ? "border-rose-200" : "border-emerald-200"}>
          <CardContent className="pt-6 text-sm">
            <p className={error ? "text-rose-700" : "text-emerald-700"}>{error || message}</p>
          </CardContent>
        </Card>
      )}

      {view === "dashboard" && (
        <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
          {[
            ["Total Agencies", dashboard?.totalAgencies ?? 0],
            ["Total Users", dashboard?.totalUsers ?? 0],
            ["Active Users", dashboard?.activeUsers ?? 0],
            ["Inactive Users", dashboard?.inactiveUsers ?? 0],
            ["Total Jobs", dashboard?.totalJobs ?? 0],
            ["Total Candidates", dashboard?.totalCandidates ?? 0],
          ].map(([label, value]) => (
            <Card key={String(label)}>
              <CardHeader className="pb-2">
                <CardDescription>{String(label)}</CardDescription>
                <CardTitle className="text-3xl">{String(value)}</CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}

      {view === "dashboard" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Recent Agencies</CardTitle>
                <CardDescription>Latest agencies created in Adam.</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={() => router.push("/admin/agencies")}>
                View all
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {(agencyPageItems || []).map((agency) => (
                <div key={agency.id} className="flex items-center justify-between rounded-2xl border border-slate-100 px-4 py-3">
                  <div>
                    <p className="font-semibold text-slate-900">{agency.name}</p>
                    <p className="text-xs text-slate-500">{agency.slug}</p>
                  </div>
                  <Badge variant={agency.status === "Active" ? "high" : "low"}>{agency.status}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Recent Users</CardTitle>
                <CardDescription>Latest users created across agencies.</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={() => router.push("/admin/users")}>
                View all
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {(userRows || []).map((item) => (
                <div key={item.id} className="flex items-center justify-between rounded-2xl border border-slate-100 px-4 py-3">
                  <div>
                    <p className="font-semibold text-slate-900">{item.name || item.email}</p>
                    <p className="text-xs text-slate-500">
                      {item.email} - {item.agencyName || "Unassigned"}
                    </p>
                  </div>
                  <Badge variant={item.status === "Active" ? "high" : "low"}>{item.role}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}

      {view === "agencies" && (
        <Card>
          <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle>Agencies</CardTitle>
              <CardDescription>Manage agency records and tenant status.</CardDescription>
            </div>
            <Button onClick={openCreateAgency}>
              <Plus className="mr-2 h-4 w-4" /> Create Agency
            </Button>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 border-b border-slate-100 pb-4 lg:flex-row">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
              <Input value={agencySearch} onChange={(event) => setAgencySearch(event.target.value)} placeholder="Search agencies" className="pl-10" />
            </div>
            <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={agencyStatus} onChange={(event) => setAgencyStatus(event.target.value)}>
              <option value="">All Status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </CardContent>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
                <tr>
                  <th className="px-6 py-4">Agency Name</th>
                  <th className="px-6 py-4">Agency ID</th>
                  <th className="px-6 py-4">Created Date</th>
                  <th className="px-6 py-4">Status</th>
                  <th className="px-6 py-4">Total Users</th>
                  <th className="px-6 py-4">Total Jobs</th>
                  <th className="px-6 py-4">Total Candidates</th>
                  <th className="px-6 py-4">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {agencyRows.map((agency) => (
                  <tr key={agency.id}>
                    <td className="px-6 py-4 font-semibold text-slate-900">{agency.name}</td>
                    <td className="px-6 py-4 text-sm text-slate-600">{agency.id}</td>
                    <td className="px-6 py-4 text-sm text-slate-600">{agency.createdAt ? new Date(agency.createdAt).toLocaleString() : "-"}</td>
                    <td className="px-6 py-4"><Badge variant={agency.status === "Active" ? "high" : "low"}>{agency.status}</Badge></td>
                    <td className="px-6 py-4 text-sm text-slate-600">{agency.totalUsers}</td>
                    <td className="px-6 py-4 text-sm text-slate-600">{agency.totalJobs}</td>
                    <td className="px-6 py-4 text-sm text-slate-600">{agency.totalCandidates}</td>
                    <td className="px-6 py-4">
                      <div className="flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={() => openEditAgency(agency)}>
                          <Pencil className="mr-2 h-4 w-4" /> Edit
                        </Button>
                        {agency.status === "Active" ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setConfirm({
                                title: "Deactivate agency",
                                description: `Deactivate ${agency.name}? Users will remain on the record, but the agency will be marked inactive.`,
                                confirmLabel: "Deactivate",
                                action: async () => {
                                  const result = await deactivateAgency(agency.id);
                                  if (!result.success) {
                                    setError(result.error || "Unable to deactivate agency.");
                                    return;
                                  }
                                  setMessage("Agency deactivated.");
                                  setConfirm(null);
                                  await refreshCurrentView();
                                },
                              })
                            }
                          >
                            <Power className="mr-2 h-4 w-4" /> Deactivate
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setConfirm({
                                title: "Reactivate agency",
                                description: `Reactivate ${agency.name}?`,
                                confirmLabel: "Reactivate",
                                action: async () => {
                                  const result = await reactivateAgency(agency.id);
                                  if (!result.success) {
                                    setError(result.error || "Unable to reactivate agency.");
                                    return;
                                  }
                                  setMessage("Agency reactivated.");
                                  setConfirm(null);
                                  await refreshCurrentView();
                                },
                              })
                            }
                          >
                            <Power className="mr-2 h-4 w-4" /> Reactivate
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PaginationBar
            page={agencyPage}
            totalPages={agencyPagination.totalPages}
            total={agencyPagination.total}
            pageSize={agencyPageSize}
            onPageChange={setAgencyPage}
            onPageSizeChange={(size) => {
              setAgencyPageSize(size);
              setAgencyPage(1);
            }}
          />
        </Card>
      )}

      {view === "users" && (
        <Card>
          <CardHeader className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <CardTitle>Users</CardTitle>
              <CardDescription>Users are grouped by agency and can be assigned a single agency only.</CardDescription>
            </div>
            <Button onClick={openCreateUser}>
              <Plus className="mr-2 h-4 w-4" /> Create User
            </Button>
          </CardHeader>
          <CardContent className="grid gap-3 border-b border-slate-100 pb-4 lg:grid-cols-4">
            <div className="relative lg:col-span-2">
              <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
              <Input value={userSearch} onChange={(event) => setUserSearch(event.target.value)} placeholder="Search users" className="pl-10" />
            </div>
            <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={userAgencyFilter} onChange={(event) => setUserAgencyFilter(event.target.value)}>
              <option value="">All agencies</option>
              {agencyOptions.map((agency) => (
                <option key={agency.id} value={agency.id}>
                  {agency.name}
                </option>
              ))}
            </select>
            <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={userRoleFilter} onChange={(event) => setUserRoleFilter(event.target.value)}>
              <option value="">All roles</option>
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={userStatusFilter} onChange={(event) => setUserStatusFilter(event.target.value)}>
              <option value="">All status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </CardContent>
          <div className="space-y-6 p-6">
            {userGroups.map((group) => (
              <div key={group.name} className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-slate-900">{group.name}</h3>
                    <p className="text-sm text-slate-500">{group.items.length} users on this page</p>
                  </div>
                </div>
                <div className="overflow-x-auto rounded-2xl border border-slate-100">
                  <table className="min-w-full divide-y divide-slate-100">
                    <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
                      <tr>
                        <th className="px-4 py-3">User Name</th>
                        <th className="px-4 py-3">Email</th>
                        <th className="px-4 py-3">Agency</th>
                        <th className="px-4 py-3">Role</th>
                        <th className="px-4 py-3">Status</th>
                        <th className="px-4 py-3">Created Date</th>
                        <th className="px-4 py-3">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 bg-white">
                      {group.items.map((item) => (
                        <tr key={item.id}>
                          <td className="px-4 py-3 font-semibold text-slate-900">{item.name || item.email}</td>
                          <td className="px-4 py-3 text-sm text-slate-600">{item.email}</td>
                          <td className="px-4 py-3 text-sm text-slate-600">{item.agencyName || item.agencyId || "-"}</td>
                          <td className="px-4 py-3"><Badge variant={isSuperAdminRole(item.role) ? "high" : "neutral"}>{item.role}</Badge></td>
                          <td className="px-4 py-3"><Badge variant={item.status === "Active" ? "high" : "low"}>{item.status}</Badge></td>
                          <td className="px-4 py-3 text-sm text-slate-600">{item.createdAt ? new Date(item.createdAt).toLocaleString() : "-"}</td>
                          <td className="px-4 py-3">
                            <Button variant="outline" size="sm" onClick={() => openEditUser(item)}>
                              <Pencil className="mr-2 h-4 w-4" /> Edit
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
          <PaginationBar
            page={userPage}
            totalPages={userPagination.totalPages}
            total={userPagination.total}
            pageSize={userPageSize}
            onPageChange={setUserPage}
            onPageSizeChange={(size) => {
              setUserPageSize(size);
              setUserPage(1);
            }}
          />
        </Card>
      )}

      <Modal
        open={agencyModalOpen}
        onOpenChange={setAgencyModalOpen}
        title={agencyModalMode === "create" ? "Create Agency" : "Edit Agency"}
        description="Create or update an agency record."
      >
        <div className="space-y-4">
          <Input value={agencyName} onChange={(event) => setAgencyName(event.target.value)} placeholder="Agency name" />
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setAgencyModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void submitAgency()}>{agencyModalMode === "create" ? "Create" : "Save"}</Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={userModalOpen}
        onOpenChange={setUserModalOpen}
        title={userModalMode === "create" ? "Create User" : "Edit User"}
        description="Assign the user to exactly one agency and one role."
      >
        <div className="grid gap-4">
          <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={userAgencyId} onChange={(event) => setUserAgencyId(event.target.value)}>
            <option value="">Select agency</option>
            {agencyOptions.map((agency) => (
              <option key={agency.id} value={agency.id}>
                {agency.name}
              </option>
            ))}
          </select>
          <Input value={userName} onChange={(event) => setUserName(event.target.value)} placeholder="Full name" />
          <Input value={userEmail} onChange={(event) => setUserEmail(event.target.value)} placeholder="Email address" />
          <select className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" value={userRole} onChange={(event) => setUserRole(event.target.value)}>
            {ROLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={userIsActive} onChange={(event) => setUserIsActive(event.target.checked)} />
            Active
          </label>
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setUserModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void submitUser()}>{userModalMode === "create" ? "Create" : "Save"}</Button>
          </div>
        </div>
      </Modal>

      <ConfirmModal confirm={confirm} onClose={() => setConfirm(null)} />
    </PageShell>
  );
}
