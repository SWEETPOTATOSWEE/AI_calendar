"use client";

import { useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";

export default function SandboxPage() {
  const [open, setOpen] = useState(false);
  const [collapsing, setCollapsing] = useState(false);

  return (
    <main className="min-h-screen bg-[#f4f6f8] px-6 py-8">
      <div className="mx-auto w-full max-w-3xl">
        <div
          className={`w-full border border-gray-200 bg-white ${
            open || collapsing ? "rounded-[32px] shadow-lg" : "rounded-full shadow-sm"
          }`}
        >
          <div className="flex items-center gap-3 px-5 py-3">
            <Search className="size-5 text-gray-600" />
            <span className="text-sm font-medium text-gray-700">검색</span>
            <button
              type="button"
              className="ml-auto flex size-8 items-center justify-center rounded-full bg-gray-100 text-gray-600 transition-colors hover:bg-gray-200"
              onClick={() => {
                if (open) {
                  setOpen(false);
                  setCollapsing(true);
                } else {
                  setOpen(true);
                  setCollapsing(false);
                }
              }}
              aria-label="고급 검색 토글"
            >
              <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
            </button>
          </div>
          <div
            className={`overflow-hidden transition-[max-height] duration-300 ease-out ${
              open ? "max-h-[520px]" : "max-h-0"
            }`}
            onTransitionEnd={(event) => {
              if (event.propertyName !== "max-height") return;
              if (!open) setCollapsing(false);
            }}
          >
            <div className="px-5 pb-4 pt-2">
              <div className="grid grid-cols-[140px_1fr] gap-x-6 gap-y-3 text-sm">
                <div className="pt-2 text-gray-700">다음에서 검색:</div>
                <div className="flex items-center gap-2">
                  <div className="inline-flex items-center gap-2 rounded-md border border-blue-500 bg-blue-50 px-3 py-2 text-blue-700">
                    <Check className="size-4" />
                    <span className="text-sm font-medium">사용 중인 캘린더</span>
                    <ChevronDown className="size-4 text-blue-600" />
                  </div>
                </div>

                <div className="pt-2 text-gray-700">제목</div>
                <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">
                  일정에 포함된 키워드
                </div>

                <div className="pt-2 text-gray-700">참석자</div>
                <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">
                  참석자, 주최자 또는 크리에이터 입력
                </div>

                <div className="pt-2 text-gray-700">장소</div>
                <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">위치 또는 회의실 입력</div>

                <div className="pt-2 text-gray-700">제외할 검색어</div>
                <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">
                  일정에 포함되지 않은 키워드
                </div>

                <div className="pt-2 text-gray-700">날짜</div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">시작 날짜</div>
                  <span className="text-gray-400">-</span>
                  <div className="rounded-md bg-gray-100 px-3 py-2 text-gray-500">종료 날짜</div>
                </div>
              </div>
              <div className="mt-4 flex items-center justify-end gap-6 text-sm">
                <button type="button" className="text-gray-500 hover:text-gray-700">
                  재설정
                </button>
                <button type="button" className="font-medium text-blue-600 hover:text-blue-700">
                  검색
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
