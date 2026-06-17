"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

export function PageTransitionBar({ loading }: { loading?: boolean }) {
  const pathname = usePathname();
  const [progress, setProgress] = useState(0);
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevPathRef = useRef(pathname);

  const start = () => {
    setVisible(true);
    setProgress(10);
    intervalRef.current = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 85) {
          clearInterval(intervalRef.current!);
          return 85;
        }
        return prev + Math.random() * 8;
      });
    }, 200);
  };

  const finish = () => {
    clearInterval(intervalRef.current!);
    setProgress(100);
    timerRef.current = setTimeout(() => {
      setVisible(false);
      setProgress(0);
    }, 400);
  };

  // Trigger on route change
  useEffect(() => {
    if (prevPathRef.current !== pathname) {
      prevPathRef.current = pathname;
      finish();
    }
  }, [pathname]);

  // Trigger on external loading prop (isSubmitting from pages)
  useEffect(() => {
    if (loading) {
      start();
    } else {
      finish();
    }

    return () => {
      clearInterval(intervalRef.current!);
      clearTimeout(timerRef.current!);
    };
  }, [loading]);

  if (!visible) return null;

  return (
    <div className="pointer-events-none fixed left-0 top-0 z-[9999] h-[3px] w-full">
      <div
        className="h-full bg-[#166534] transition-all duration-300 ease-out"
        style={{ width: `${progress}%`, opacity: visible ? 1 : 0 }}
      />
    </div>
  );
}
