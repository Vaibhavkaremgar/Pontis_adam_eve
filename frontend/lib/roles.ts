const SUPER_ADMIN_ROLES = new Set(["superadmin", "SUPER_ADMIN", "super_admin", "admin", "internal_ops"]);

export function normalizeRole(role: string | null | undefined): string {
  const value = String(role || "").trim();
  if (!value) return "";
  const lowered = value.toLowerCase();
  if (SUPER_ADMIN_ROLES.has(value) || SUPER_ADMIN_ROLES.has(lowered)) {
    return "superadmin";
  }
  return lowered;
}

export function isSuperAdminRole(role: string | null | undefined): boolean {
  return normalizeRole(role) === "superadmin";
}

export function toAdminRoleValue(role: string | null | undefined): string {
  const normalized = normalizeRole(role);
  if (normalized === "superadmin") return "SUPER_ADMIN";
  return "AGENCY_USER";
}
