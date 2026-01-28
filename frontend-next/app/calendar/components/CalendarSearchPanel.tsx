"use client";

import { useMemo } from "react";
import { Calendar as CalendarIcon, ChevronDown, Search, Undo2, X } from "lucide-react";
import type { CalendarEvent } from "../lib/types";
import { DatePopover } from "./DatePopover";

type CalendarSearchFilters = {
  title: string;
  attendees: string;
  location: string;
  exclude: string;
  startDate: string;
  endDate: string;
};

type CalendarSearchPanelProps = {
  showHeader?: boolean;
  onClose?: () => void;
  filters: CalendarSearchFilters;
  advancedOpen: boolean;
  onToggleAdvanced: () => void;
  onChangeFilters: (next: Partial<CalendarSearchFilters>) => void;
  onResetAdvancedFilters: () => void;
  onBasicSearch: () => Promise<void> | void;
  onKeyDownAdvanced: (event: React.KeyboardEvent) => void;
  searchResultsOpen: boolean;
  searchResults: CalendarEvent[];
  onFocusResult: (event: CalendarEvent) => void;
  getEventStartDate: (value?: string | null) => Date | null;
  formatSearchDate: (value: Date) => string;
  getResultAccentColor: (event: CalendarEvent) => string;
};

export default function CalendarSearchPanel({
  showHeader = false,
  onClose,
  filters,
  advancedOpen,
  onToggleAdvanced,
  onChangeFilters,
  onResetAdvancedFilters,
  onBasicSearch,
  onKeyDownAdvanced,
  searchResultsOpen,
  searchResults,
  onFocusResult,
  getEventStartDate,
  formatSearchDate,
  getResultAccentColor,
}: CalendarSearchPanelProps) {
  const groupedResults = useMemo(() => {
    if (!searchResultsOpen || searchResults.length === 0) return [] as Array<[string, CalendarEvent[]]>;
    const groups = new Map<string, CalendarEvent[]>();
    searchResults.forEach((event) => {
      const date = getEventStartDate(event.start);
      const key = date ? formatSearchDate(date) : "날짜 미지정";
      const bucket = groups.get(key);
      if (bucket) {
        bucket.push(event);
      } else {
        groups.set(key, [event]);
      }
    });
    return Array.from(groups.entries());
  }, [searchResultsOpen, searchResults, getEventStartDate, formatSearchDate]);

  return (
    <div className="flex h-full flex-col overflow-hidden min-h-0">
      {showHeader && (
        <div className="flex items-center justify-between px-3 pt-3 pb-2">
          <h3 className="text-[18px] font-semibold text-text-primary">일정 검색</h3>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-full hover:bg-bg-subtle transition-colors text-text-secondary"
            onClick={onClose}
            aria-label="닫기"
          >
            <X className="size-5" />
          </button>
        </div>
      )}
      <div className="px-3 py-2">
        <div className="flex items-center gap-2 rounded-xl border border-border-subtle bg-bg-canvas px-3 py-1.5 shadow-sm">
          <Search className="size-4 text-text-secondary" />
          <input
            className="flex-1 bg-transparent text-[13px] text-text-primary focus:outline-none placeholder:text-text-disabled"
            placeholder="일정 키워드 입력"
            value={filters.title}
            onChange={(event) => onChangeFilters({ title: event.target.value })}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void onBasicSearch();
              }
            }}
            aria-label="기본 검색어"
          />
          <button
            type="button"
            className={`flex size-7 items-center justify-center rounded-lg transition-colors hover:bg-bg-subtle ${
              advancedOpen ? "text-token-primary bg-bg-subtle" : "text-text-secondary"
            }`}
            onClick={onToggleAdvanced}
            aria-label="고급 검색 토글"
            aria-expanded={advancedOpen}
          >
            <ChevronDown className={`size-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {advancedOpen && (
        <div className="px-3 pb-4 border-b border-border-subtle animate-in slide-in-from-top-1 duration-200">
          <div className="space-y-3 pt-1">
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">참석자</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="참석자, 주최자 입력"
                value={filters.attendees}
                onChange={(event) => onChangeFilters({ attendees: event.target.value })}
                onKeyDown={onKeyDownAdvanced}
              />
            </div>
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">장소</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="위치 또는 회의실"
                value={filters.location}
                onChange={(event) => onChangeFilters({ location: event.target.value })}
                onKeyDown={onKeyDownAdvanced}
              />
            </div>
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">제외할 검색어</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="제외할 키워드"
                value={filters.exclude}
                onChange={(event) => onChangeFilters({ exclude: event.target.value })}
                onKeyDown={onKeyDownAdvanced}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <div className="text-[11px] font-medium text-text-secondary px-1">시작일</div>
                <DatePopover
                  className="w-full"
                  value={filters.startDate}
                  onChange={(val) => onChangeFilters({ startDate: val })}
                  label="시작일"
                  icon={<CalendarIcon className="size-3.5" />}
                  placeholder="시작일"
                />
              </div>
              <div className="space-y-1">
                <div className="text-[11px] font-medium text-text-secondary px-1">종료일</div>
                <DatePopover
                  className="w-full"
                  value={filters.endDate}
                  onChange={(val) => onChangeFilters({ endDate: val })}
                  label="종료일"
                  icon={<CalendarIcon className="size-3.5" />}
                  placeholder="종료일"
                />
              </div>
            </div>
            <div className="flex justify-end items-center pt-2">
              <button
                type="button"
                className="text-[11px] text-text-tertiary hover:text-text-secondary transition-colors inline-flex items-center gap-1"
                onClick={onResetAdvancedFilters}
              >
                <Undo2 className="size-3" /> 필터 초기화
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-2 pb-10">
        {searchResultsOpen && (
          searchResults.length === 0 ? (
            <div className="py-10 text-center text-xs text-text-tertiary">검색 결과가 없습니다.</div>
          ) : (
            groupedResults.map(([dateLabel, events], gIndex) => (
              <div key={`group-${dateLabel}-${gIndex}`} className="mb-4">
                <div className="px-1 mb-1 text-[13px] font-medium text-text-primary border-b border-border-subtle pb-1">
                  {dateLabel}
                </div>
                <div className="space-y-0.5">
                  {events.map((event, index) => {
                    const accentColor = getResultAccentColor(event);
                    return (
                      <div
                        key={`search-result-drawer-${event.id}-${index}`}
                        className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-[14px] text-text-primary hover:bg-bg-subtle transition-colors cursor-pointer"
                        onClick={() => onFocusResult(event)}
                      >
                        <div
                          className="w-[3px] self-stretch my-0.5 rounded-full shrink-0"
                          style={{ backgroundColor: accentColor }}
                        />
                        <div className="flex-1 flex flex-col min-w-0">
                          <div className="font-medium truncate">{event.title}</div>
                          {event.location && (
                            <div className="text-[12px] text-text-secondary truncate">{event.location}</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )
        )}
      </div>
    </div>
  );
}
