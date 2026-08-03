import AdminPageClient from "../admin-page-client";
import { Suspense } from "react";

export const dynamic = "force-dynamic";

export default function AdminAgenciesPage() {
  return (
    <Suspense fallback={<div className="mx-auto w-full max-w-7xl px-4 py-6 text-sm text-gray-600">Loading agencies...</div>}>
      <AdminPageClient view="agencies" />
    </Suspense>
  );
}
