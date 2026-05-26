"use client";

import Link from "next/link";
import { cn } from "@/lib/utils";

const ITEMS = [
  { href: "/review", label: "Review" },
  { href: "/ready", label: "Ready" },
  { href: "/results", label: "Results" },
  { href: "/offers", label: "Offers", disabled: true },
  { href: "/closed", label: "Closed", disabled: true },
];

type ResultsPipelineNavProps = {
  active: "Review" | "Ready" | "Results" | "Offers" | "Closed";
};

export function ResultsPipelineNav({ active }: ResultsPipelineNavProps) {
  return (
    <div className="border-b border-slate-200 bg-white/75 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1600px] items-center gap-2 overflow-x-auto px-4 py-3 sm:px-6 lg:px-8">
        {ITEMS.map((item) => {
          const activeItem = item.label === active;
          const baseClass =
            "inline-flex items-center rounded-full px-4 py-2 text-sm font-medium transition-all whitespace-nowrap";
          const variantClass = activeItem
            ? "bg-[#0F172A] text-white shadow-[0_12px_28px_rgba(15,23,42,0.22)]"
            : item.disabled
              ? "cursor-not-allowed bg-slate-100 text-slate-400"
              : "bg-white text-slate-600 hover:bg-slate-100 hover:text-slate-900";

          if (item.disabled) {
            return (
              <span key={item.label} className={cn(baseClass, variantClass)}>
                {item.label}
              </span>
            );
          }

          return (
            <Link key={item.label} href={item.href} className={cn(baseClass, variantClass)}>
              {item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

