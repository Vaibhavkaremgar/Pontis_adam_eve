"use client";

/**
 * What this component does:
 * Renders lightweight listening animation for active microphone capture.
 *
 * Which state it depends on:
 * Depends on `isActive` derived from callStatus === "listening".
 */
import { motion } from "framer-motion";

export function WaveAnimation({ isActive }: { isActive: boolean }) {
  return (
    <div className="flex items-center gap-2">
      {[0, 1, 2].map((index) => (
        <motion.span
          key={index}
          className="h-2.5 w-2.5 rounded-full bg-[#1F6F4A]"
          animate={
            isActive
              ? {
                  y: [0, -6, 0],
                  opacity: [0.45, 1, 0.45]
                }
              : {
                  y: 0,
                  opacity: 0.35
                }
          }
          transition={{
            duration: 0.6,
            repeat: isActive ? Infinity : 0,
            delay: index * 0.08,
            ease: "easeInOut"
          }}
        />
      ))}
    </div>
  );
}
