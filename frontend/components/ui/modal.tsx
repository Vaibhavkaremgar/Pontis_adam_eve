"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

type ModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  description?: string;
  children: React.ReactNode;
  hideClose?: boolean;
  className?: string;
};

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  hideClose,
  className
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay fixed inset-0 z-50 bg-gray-900/40" />
        <Dialog.Content
          className={cn(
            "modal-content fixed left-1/2 top-1/2 z-50 w-[92vw] max-w-xl -translate-x-1/2 -translate-y-1/2 rounded-xl border border-[#E5E7EB] bg-white p-6 shadow-sm",
            className
          )}
        >
          {(title || description) && (
            <div className="mb-4">
              {title && <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>}
              {description && (
                <Dialog.Description className="text-sm text-gray-600">
                  {description}
                </Dialog.Description>
              )}
            </div>
          )}
          {!hideClose && (
            <Dialog.Close className="absolute right-4 top-4 rounded-md p-1 text-gray-500 hover:bg-gray-100">
              <X className="h-4 w-4" />
            </Dialog.Close>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
