export default function Home() {
  return (
    <main className="min-h-screen p-6 flex items-center justify-center">
      <section className="w-full max-w-xl space-y-4">
        <div className="space-y-2">
          <h1 className="text-xl font-semibold">Calendar</h1>
          <p className="text-sm">
            구글 캘린더 연동 또는 Admin 모드로 시작합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <a className="inline-flex items-center border px-3 py-2" href="/auth/google/login">
            Google로 로그인
          </a>
          <a className="inline-flex items-center border px-3 py-2" href="/admin">
            Admin으로 접속
          </a>
        </div>
        <p className="text-xs">
          자동 로그인을 원하면 위 버튼을 누르세요. (테스트 환경)
        </p>
      </section>
    </main>
  );
}
