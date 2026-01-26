# Google Cloud Console 설정 가이드

## 1. Google Cloud 프로젝트 생성

1. [Google Cloud Console](https://console.cloud.google.com/) 접속
2. 새 프로젝트 생성 또는 기존 프로젝트 선택
3. 프로젝트 이름 입력 (예: "AI Calendar")

## 2. API 활성화

1. 좌측 메뉴에서 **APIs & Services** → **Library** 선택
2. 다음 API들을 검색하여 활성화:
   - **Google Calendar API** - 일정 관리용
   - **Google Tasks API** - 작업 관리용 (선택)

## 3. OAuth 동의 화면 설정

1. 좌측 메뉴에서 **APIs & Services** → **OAuth consent screen** 선택
2. User Type 선택:
   - **External** 선택 (개인/테스트용)
   - Create 클릭
3. App information 입력:
   - **App name**: AI Calendar
   - **User support email**: 본인 이메일
   - **Developer contact information**: 본인 이메일
4. Scopes 설정:
   - **Add or Remove Scopes** 클릭
   - 다음 scope들을 추가:
     ```
     .../auth/calendar.events
     .../auth/calendar.readonly
     .../auth/tasks
     openid
     profile
     ```
   - Update 클릭
5. Test users 추가 (External 타입인 경우):
   - **Add Users** 클릭
   - 테스트할 Google 계정 이메일 추가
6. Summary 확인 후 완료

## 4. OAuth 2.0 클라이언트 ID 생성

1. 좌측 메뉴에서 **APIs & Services** → **Credentials** 선택
2. **+ CREATE CREDENTIALS** → **OAuth client ID** 클릭
3. Application type:
   - **Web application** 선택
4. Name 입력:
   - 예: "AI Calendar Web Client"

### 5. Authorized redirect URIs 설정

**중요**: 리디렉션 URI는 환경에 따라 다릅니다.

#### Codespaces 환경

Codespaces는 URL이 동적으로 변경되므로 **와일드카드**를 사용할 수 없습니다.
다음 두 가지 방법 중 하나를 선택하세요:

**방법 1: 현재 Codespace URL 추가 (권장)**

현재 Codespace의 URL을 확인:
```bash
echo "https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/auth/google/callback"
```

출력된 URL을 **Authorized redirect URIs**에 추가합니다.

**Example**:
```
https://your-codespace-name-3000.app.github.dev/auth/google/callback
```

**주의**: Codespace를 새로 만들면 URL이 바뀌므로, 새 URL을 추가해야 합니다.

**방법 2: 여러 Codespace URL 미리 추가**

여러 개의 Codespace를 사용할 예정이라면 각각의 URL을 모두 추가할 수 있습니다:
```
https://codespace1-name-3000.app.github.dev/auth/google/callback
https://codespace2-name-3000.app.github.dev/auth/google/callback
https://codespace3-name-3000.app.github.dev/auth/google/callback
```

#### 로컬 개발 환경

로컬에서 테스트하는 경우 다음을 추가:
```
http://localhost:3000/auth/google/callback
```

#### 프로덕션 환경

실제 배포 도메인이 있다면:
```
https://your-domain.com/auth/google/callback
```

### 모든 환경을 위한 설정 예시

```
http://localhost:3000/auth/google/callback
https://your-codespace-name-3000.app.github.dev/auth/google/callback
https://your-production-domain.com/auth/google/callback
```

6. **CREATE** 클릭

## 6. 클라이언트 정보 저장

생성 완료 후 표시되는 정보를 저장합니다:

- **Client ID**: `your-client-id.apps.googleusercontent.com`
- **Client Secret**: `GOCSPX-xxxxxxxxxxxxxxxxxxxxx`

이 정보를 **Codespaces Secrets**에 저장:
- `GOOGLE_CLIENT_ID`: Client ID 값
- `GOOGLE_CLIENT_SECRET`: Client Secret 값

## 7. Codespaces Secrets 설정

1. GitHub 저장소 → **Settings** → **Secrets and variables** → **Codespaces**
2. **New repository secret** 클릭
3. 다음 시크릿 추가:

```
Name: GOOGLE_CLIENT_ID
Value: [위에서 복사한 Client ID]

Name: GOOGLE_CLIENT_SECRET
Value: [위에서 복사한 Client Secret]

Name: OPENAI_API_KEY
Value: [OpenAI API 키]
```

## 8. 테스트

1. Codespace 재시작 (환경 변수 적용)
2. 애플리케이션 실행:
   ```bash
   /workspaces/AI_calendar/scripts/dev-run.sh
   ```
3. 프론트엔드 URL 접속 (출력된 FRONTEND_BASE_URL)
4. Google 로그인 테스트

## 문제 해결

### "redirect_uri_mismatch" 에러

Google OAuth 에러: `redirect_uri_mismatch`

**원인**: Google Cloud Console에 등록된 Redirect URI와 실제 요청 URI가 다름

**해결**:
1. 에러 메시지에서 실제 사용된 redirect_uri 확인
2. Google Cloud Console → Credentials → OAuth 2.0 Client ID 수정
3. 해당 URI를 **Authorized redirect URIs**에 추가
4. 저장 후 5분 정도 기다림 (전파 시간)

### Codespace URL 확인 방법

현재 Codespace의 리디렉션 URI 확인:
```bash
echo "https://${CODESPACE_NAME}-3000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/auth/google/callback"
```

### 새 Codespace에서 작업할 때

1. 위 명령어로 새 URL 확인
2. Google Cloud Console에서 새 URL 추가
3. 5분 후 테스트

## 참고 링크

- [Google Cloud Console](https://console.cloud.google.com/)
- [OAuth 2.0 설정 가이드](https://developers.google.com/identity/protocols/oauth2)
- [Google Calendar API](https://developers.google.com/calendar)
