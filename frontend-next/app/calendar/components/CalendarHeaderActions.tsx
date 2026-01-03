"use client";

import type { AuthStatus } from "../lib/types";
import { enterAdmin, exitAdmin, loginGoogle, logout } from "../lib/api";

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

  return (
    <div className={containerClassName}>
      {googleEnabled && !hasToken && (
        <button
          className={`px-3 py-1 rounded-full text-sm font-semibold border border-blue-200 text-blue-600 hover:bg-blue-50${buttonSuffix}`}
          type="button"
          onClick={loginGoogle}
        >
          Google 로그인
        </button>
      )}
      {hasToken && (
        <button
          className={`px-3 py-1 rounded-full text-sm font-semibold border border-gray-200 text-gray-700 hover:bg-gray-50${buttonSuffix}`}
          type="button"
          onClick={logout}
        >
          로그아웃
        </button>
      )}
      {!hasToken && (
        <>
          <button
            className={`px-3 py-1 rounded-full text-sm font-semibold border border-gray-200 text-gray-700 hover:bg-gray-50${buttonSuffix}`}
            type="button"
            onClick={enterAdmin}
          >
            관리자
          </button>
          <button
            className={`px-3 py-1 rounded-full text-sm font-semibold border border-gray-200 text-gray-700 hover:bg-gray-50${buttonSuffix}`}
            type="button"
            onClick={exitAdmin}
          >
            관리자 해제
          </button>
        </>
      )}
    </div>
  );
}
