"use client";

import type { AuthStatus } from "../lib/types";
import { enterAdmin, exitAdmin, loginGoogle, logout } from "../lib/api";

export default function CalendarHeaderActions({ status }: { status: AuthStatus | null }) {
  const googleEnabled = Boolean(status?.enabled && status?.configured);
  const hasToken = Boolean(status?.has_token);

  return (
    <div className="flex items-center gap-2">
      {googleEnabled && !hasToken && (
        <button
          className="px-3 py-1 rounded-full text-xs font-semibold border border-blue-200 text-blue-600 hover:bg-blue-50"
          type="button"
          onClick={loginGoogle}
        >
          Google 로그인
        </button>
      )}
      {hasToken && (
        <button
          className="px-3 py-1 rounded-full text-xs font-semibold border border-gray-200 text-slate-600 hover:bg-gray-50"
          type="button"
          onClick={logout}
        >
          로그아웃
        </button>
      )}
      {!hasToken && (
        <>
          <button
            className="px-3 py-1 rounded-full text-xs font-semibold border border-gray-200 text-slate-600 hover:bg-gray-50"
            type="button"
            onClick={enterAdmin}
          >
            관리자
          </button>
          <button
            className="px-3 py-1 rounded-full text-xs font-semibold border border-gray-200 text-slate-400 hover:bg-gray-50"
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
