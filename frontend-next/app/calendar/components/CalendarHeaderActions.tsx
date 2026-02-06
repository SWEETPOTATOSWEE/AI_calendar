"use client";

import { useEffect, useRef, useState } from "react";
import { User } from "lucide-react";
import type { AuthStatus } from "../lib/types";
import { loginGoogle, logout } from "../lib/api";

type CalendarHeaderActionsProps = {
  status: AuthStatus | null;
  className?: string;
  buttonClassName?: string;
};

export default function CalendarHeaderActions({
  status,
  className,
  buttonClassName,
}: CalendarHeaderActionsProps) {
  const googleEnabled = Boolean(status?.enabled && status?.configured);
  const hasToken = Boolean(status?.has_token);
  const containerClassName = className ?? "flex items-center gap-2";
  const buttonSuffix = buttonClassName ? ` ${buttonClassName}` : "";
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [profileImageError, setProfileImageError] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement | null>(null);
  const photoUrl = status?.photo_url ?? null;

  useEffect(() => {
    setProfileImageError(false);
  }, [photoUrl]);

  useEffect(() => {
    if (!profileMenuOpen) return;
    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node | null;
      if (!profileMenuRef.current || !target) return;
      if (!profileMenuRef.current.contains(target)) {
        setProfileMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setProfileMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("touchstart", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("touchstart", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [profileMenuOpen]);

  return (
    <div className={containerClassName}>
      {googleEnabled && !hasToken && (
        <button
          className={`px-3 py-1 rounded-full text-sm font-semibold border border-token-primary/30 text-token-primary hover:bg-token-primary/10${buttonSuffix}`}
          type="button"
          onClick={loginGoogle}
        >
          Google 로그인
        </button>
      )}
      {hasToken && (
        <div className="relative" ref={profileMenuRef}>
          <button
            className="flex size-9 items-center justify-center overflow-hidden rounded-full border border-border-subtle bg-bg-surface text-text-secondary transition-colors hover:bg-bg-subtle"
            type="button"
            onClick={() => setProfileMenuOpen((prev) => !prev)}
            aria-label="프로필 메뉴"
            aria-haspopup="menu"
            aria-expanded={profileMenuOpen}
          >
            {photoUrl && !profileImageError ? (
              <img
                src={photoUrl}
                alt="프로필 사진"
                className="size-full object-cover"
                onError={() => setProfileImageError(true)}
              />
            ) : (
              <User className="size-4" />
            )}
          </button>
          {profileMenuOpen && (
            <div
              className="absolute right-0 mt-2 z-50 w-max max-w-[calc(100vw-2rem)] sm:right-auto sm:left-0"
              role="menu"
            >
              <div
                className="min-w-full overflow-hidden whitespace-nowrap popover-surface popover-animate border border-border-subtle bg-bg-surface shadow-lg"
                data-side="bottom"
                data-align="start"
              >
                <button
                  type="button"
                  className="flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left font-medium text-text-primary transition-colors hover:bg-bg-subtle"
                  onClick={() => {
                    setProfileMenuOpen(false);
                    logout();
                  }}
                  role="menuitem"
                >
                  로그아웃
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
