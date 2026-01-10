import Link from "next/link";

const SANDBOXES = [
  {
    id: "1",
    title: "검색 유틸",
    description: "고급 검색 패널 전개/접기와 애니메이션 확인용",
  },
  {
    id: "2",
    title: "일정 개요 팝업",
    description: "일정 요약 팝업 UI 미리보기",
  },
  ...Array.from({ length: 8 }, (_, index) => {
    const id = String(index + 3);
    return {
      id,
      title: `샌드박스 ${id}`,
      description: "추가 샘플 UI 페이지",
    };
  }),
];

export default function SandboxIndexPage() {
  return (
    <main className="min-h-screen bg-[#f2f1ed] text-[#1f1e1b]">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-[#9a938a]">Sandbox</p>
            <h1 className="mt-2 font-display text-3xl font-semibold">
              샌드박스 생성
            </h1>
            <p className="mt-2 text-sm text-[#6b6460]">
              버튼을 눌러 서로 다른 샌드박스 URL로 이동합니다.
            </p>
          </div>
          <div className="rounded-full border border-[#d9d4cc] bg-white px-4 py-2 text-xs text-[#6b6460] shadow-sm">
            sandbox/1 ~ sandbox/10
          </div>
        </header>

        <section className="mt-10 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {SANDBOXES.map((sandbox) => (
            <Link
              key={sandbox.id}
              href={`/sandbox/${sandbox.id}`}
              className="group flex h-full flex-col justify-between rounded-[24px] border border-[#e3dfd7] bg-white/80 p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
            >
              <div>
                <p className="text-xs uppercase tracking-[0.2em] text-[#9a938a]">
                  Sandbox {sandbox.id}
                </p>
                <h2 className="mt-3 font-display text-xl font-semibold">
                  {sandbox.title}
                </h2>
                <p className="mt-2 text-sm text-[#6b6460]">{sandbox.description}</p>
              </div>
              <div className="mt-6 inline-flex w-fit items-center gap-2 rounded-full border border-[#d9d4cc] bg-white px-4 py-2 text-xs font-semibold text-[#1f1e1b]">
                /sandbox/{sandbox.id}
              </div>
            </Link>
          ))}
        </section>
      </div>
    </main>
  );
}
