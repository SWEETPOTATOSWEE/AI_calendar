export default function SettingsPage() {
  return (
    <main className="min-h-screen p-6">
      <div className="mx-auto max-w-2xl space-y-6">
        <header className="flex items-center justify-between gap-4">
          <h1 className="text-xl font-semibold">설정</h1>
          <a className="inline-flex items-center border px-3 py-2" href="/calendar">
            캘린더로 돌아가기
          </a>
        </header>
        <section className="border p-4">
          <h2 className="text-base font-semibold">일반</h2>
          <p className="text-sm">설정 항목은 준비 중입니다.</p>
        </section>
      </div>
    </main>
  );
}
