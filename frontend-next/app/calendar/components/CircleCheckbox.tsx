"use client";

import { Check } from "lucide-react";

type CircleCheckboxProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  className?: string;
  disabled?: boolean;
};

export default function CircleCheckbox({ checked, onChange, className = "", disabled = false }: CircleCheckboxProps) {
  return (
    <button
      type="button"
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      className={`
        inline-flex items-center justify-center
        rounded-full size-5
        border-2 transition-all
        ${
          checked
            ? "bg-primary border-primary"
            : "bg-transparent border-border-subtle hover:border-primary"
        }
        ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}
        ${className}
      `}
      aria-label={checked ? "완료됨" : "미완료"}
      aria-checked={checked}
      role="checkbox"
    >
      {checked && <Check className="size-3 text-white stroke-[3]" />}
    </button>
  );
}
