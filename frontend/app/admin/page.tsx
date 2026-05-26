import { Suspense } from "react";

export const dynamic = "force-dynamic";

import AdminPageClient from "./admin-page-client";

export default function AdminPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-7xl px-4 py-6 text-sm text-gray-600">Loading admin dashboard...</div>}>
      <AdminPageClient />
    </Suspense>
  );
}
