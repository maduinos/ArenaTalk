> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-08-27<br>
> https://maduinos.blogspot.com/

# 에이전트 CLI 설치와 로그인

ArenaTalk은 자체 모델을 갖고 있지 않다. 이미 깔려 있는 **에이전트 CLI**를
프로세스로 불러 캐릭터의 발언을 만든다. 따라서 토론이 돌려면 두 가지가
동시에 참이어야 한다.

1. CLI가 **설치**돼 있다 (`PATH`에서 찾을 수 있다)
2. 그 CLI가 **로그인**돼 있다

둘은 다른 문제이고, 예전에는 1번만 확인했다. 그래서 로그인한 적 없는
`claude`가 깔린 PC에서는 그 CLI가 무료 CLI를 밀어내고 선택된 뒤 모든 턴이
`claude failed: 1`로 죽었다. 지금은 로그인 상태까지 확인한다.

## 지원 CLI

| 이름 | 등급 | 상태 확인 방법 | 로그인 명령 |
| --- | --- | --- | --- |
| Claude Code (`claude`) | 유료 | `claude auth status --json` (`loggedIn` + `subscriptionType`) | `claude auth login` |
| OpenAI Codex (`codex`) | 유료 | `codex login status` | `codex login` |
| Cursor Agent (`cursor-agent`) | 유료 | `cursor-agent status` | `cursor-agent login` |
| Gemini CLI (`gemini`) | 무료 | `~/.gemini/oauth_creds.json` 또는 `GEMINI_API_KEY` | `gemini` 실행 후 `/auth` |
| Qwen Code (`qwen`) | 무료 | `~/.qwen/oauth_creds.json` 또는 `DASHSCOPE_API_KEY` | `qwen` 실행 후 `/auth` |
| Ollama (`ollama`) | 무료 | `ollama list` (서버 응답 + 모델 1개 이상) | 로그인 없음 |

확인은 전부 **모델 호출이 없는** 값싼 검사다. 토큰을 쓰지 않는다.

## 상태는 세 가지다 — 로그인만으로는 부족하다

`claude` / `codex` / `cursor`는 **무료 티어가 없다.** 무료 계정으로 로그인하면
`auth status`는 정상으로 나오고 실제 턴마다 실패한다. 그래서 상태를 셋으로 나눈다.

| 상태 | 뜻 | 자동 할당 | 조치 |
| --- | --- | --- | --- |
| `사용 가능` | 로그인 + 요금제 OK | ○ | - |
| `로그인 필요` | 설치만 됨 | ✕ | 로그인 |
| `요금제 필요` | 로그인은 됐지만 무료 계정/크레딧 없음 | ✕ | 무료 CLI로 전환 |
| `확인 불가` | 프로브가 답을 못 받음 | ○ | - (확인 못 한 걸로 멀쩡한 CLI를 막지 않는다) |

**`요금제 필요`를 언제 알 수 있나:**

- **Claude Code** — 미리 안다. `auth status --json`의 `subscriptionType`이
  `free`/`none`이면 표시한다. 단 이 필드가 아예 없으면 `사용 가능`으로 둔다 —
  판단 못 하는 걸로 실격시키지 않는다. `authMethod`가 `claude.ai`가 아니면
  (console / Bedrock / Vertex) 토큰 과금이므로 요금제와 무관하게 `사용 가능`.
- **Codex · Cursor Agent** — **미리 알 수 없다.** 둘 다 상태 출력에 요금제
  정보가 없다 (`Logged in using ChatGPT`가 전부). 그래서 첫 턴이 실패할 때
  에러 문구로 판정하고 ("credit balance", "402", "requires a paid" 등)
  재시도 없이 무료 CLI 안내를 붙인다. 일시적인 rate limit은 여기서 제외한다.

## 어떤 CLI가 토론에 쓰이는가

두 가지가 다릅니다.

- **선택** — `--backend all`(자동 할당)이 실제로 쓰는 목록. **유료 우선**입니다:
  로그인된 유료 CLI가 하나라도 있으면 그것들만 쓰고, 없으면 로그인된 무료 CLI를
  씁니다. 로그인 안 된 CLI는 어느 쪽이든 제외됩니다.
- **목록** — 설치된 CLI 전부. 「에이전트 설정」 창과 `arenatalk backends`는 이쪽을
  보여줍니다.

즉 유료가 로그인돼 있으면 무료 CLI는 자동 할당에서 빠지지만, 목록에는 남아 있고
백엔드 드롭다운이나 `--backend gemini`로 직접 고르면 그대로 쓸 수 있습니다.
로그인도 목록 쪽에서 하므로, 자동 할당에서 빠진 CLI에도 로그인할 수 있습니다.

```
$ arenatalk backends
 #  name    login      자동 할당
 1  claude  로그인됨   ○
 2  codex   로그인됨   ○
 3  gemini  로그인됨   -      ← 설치·로그인돼 있지만 유료가 우선이라 자동 할당엔 빠짐
```

## 언제 물어보는가

앱 시작 시 **쓸 수 있는 CLI가 하나도 없을 때만** 묻습니다. 하나라도 있으면
조용히 넘어갑니다.

- 설치는 됐는데 전부 로그인 안 됨 → 「지금 로그인하시겠습니까?」
- 아무것도 없음 → 「무료 CLI를 자동으로 설치하시겠습니까?」

`ARENATALK_NO_AGENT_PROMPT=1` 이면 묻지 않습니다 (헤드리스·CI용).

## GUI에서 (권장)

툴바의 **「에이전트 설정」** 버튼.

- **다시 검사** — 설치/로그인 상태를 다시 읽는다
- **무료 CLI 자동 설치** — Node.js가 없으면 먼저 설치하고, 그다음
  Gemini CLI → Qwen Code 순으로 시도한다. 하나라도 성공하면 멈춘다
  (Ollama는 모델이 수 GB라 마지막 수단)
- **선택 항목 로그인** — 고른 CLI의 로그인 명령을 **새 터미널 창**으로 연다.
  브라우저 승인이 끝나면 「다시 검사」

앱을 켰을 때 쓸 수 있는 CLI가 하나도 없으면 이 창을 열지 물어본다.

## CLI에서

```bash
arenatalk backends          # 설치 + 로그인 상태 표
arenatalk login --status    # 로그인 상태만
arenatalk login             # 로그인 안 된 CLI 전부 로그인 창 열기
arenatalk login claude      # 하나만
arenatalk setup             # 없으면 무료 CLI 자동 설치
arenatalk setup --no-install
```

## 자동 설치는 반드시 승인을 받는다

전역 패키지가 설치되고, 경우에 따라 수 GB 모델까지 내려받는다. 그래서
`auto_install=True`만으로는 아무것도 설치되지 않는다 — `confirm` 콜백이
계획을 받아 `True`를 돌려줘야 진행한다.

- **GUI** — 「무료 CLI 자동 설치」를 누르면 설치 항목을 나열한 확인창이 먼저 뜬다
- **CLI** — `arenatalk setup`이 목록을 보여주고 `[y/N]`를 묻는다.
  비대화형(파이프·스크립트)이면 묻지 못하므로 설치하지 않고 `--yes`를 안내한다
- `arenatalk setup --yes` / `arenatalk install --yes` 로 건너뛸 수 있다

## 설치 순서

`qwen → gemini → ollama`. 먼저 성공하는 하나에서 멈춘다.

qwen이 1순위인 이유는 무료 OAuth 티어가 살아 있어서다. Gemini CLI의 개인용
Code Assist 경로는 현재 `IneligibleTierError`를 돌려주며 중단됐고
(`GEMINI_API_KEY`를 넣는 방법은 남아 있다), ollama는 가장 확실하지만
모델이 수 GB라 마지막이다.

## 플랫폼별 자동 설치 경로

| | Node.js | Qwen/Gemini | Ollama |
| --- | --- | --- | --- |
| Windows | `winget install OpenJS.NodeJS.LTS` | `cmd /c npm install -g` | `winget install Ollama.Ollama` |
| macOS | `brew install node` | `npm install -g` | `brew install ollama` |
| Linux | **nvm → `~/.nvm`** (sudo 불필요) | `npm install -g` | 공식 `install.sh` (**sudo 필요**) |

`winget`이 없는 Windows(구버전 10 등)에서는 https://nodejs.org 에서 LTS를
직접 설치한 뒤 「무료 CLI 자동 설치」를 다시 누르면 된다.

리눅스의 ollama만은 관리자 권한이 필요해 앱에서 진행할 수 없다. 그 경우
터미널에서 `curl -fsSL https://ollama.com/install.sh | sh` 를 안내한다.

## Node.js 버전 — 우분투의 함정

두 npm CLI 모두 top-level await를 쓰는 ESM이라 **Node v20 이상**이 필요하다.
Ubuntu 22.04의 `apt install nodejs`는 **v12**를 깐다. npm은 멀쩡히 있으므로
"npm 있음 → 설치 진행"만 보면 설치는 성공하고 첫 실행에서

```
SyntaxError: Unexpected reserved word
```

로 죽는다. 그래서 `node --version`까지 확인하고, 낮으면 npm 설치를 건너뛰고
nvm으로 최신 LTS를 먼저 깐다. nvm은 `$NVM_DIR`(기본 `~/.nvm`) 안에서만 동작하고
`PROFILE=/dev/null`로 사용자의 `.bashrc`를 건드리지 않는다. `_path_extras`가
`~/.nvm/versions/node/*/bin`을 이미 훑으므로 **재시작 없이** 곧바로 잡힌다.

버전을 못 읽으면(`None`) 막지 않는다 — 확인 못 한 걸로 실격시키지 않는 원칙.

## PATH 이야기

GUI는 로그인 셸이 아니라 데스크톱 세션에서 뜨기 때문에, `npm -g`가 깔아 둔
디렉터리가 `PATH`에 없는 경우가 흔하다. ArenaTalk은 CLI를 찾을 때와 실행할 때
아래를 `PATH` 앞에 붙인다.

- 공통: `~/.local/bin`, `~/bin`
- Windows: `%APPDATA%\npm`, `%ProgramFiles%\nodejs`, `%LOCALAPPDATA%\Ollama`,
  `%LOCALAPPDATA%\Microsoft\WinGet\Links`
- macOS/Linux: `/usr/local/bin`, `/opt/homebrew/bin`, `~/.nvm/versions/node/*/bin`,
  `~/.npm-global/bin`, `~/.volta/bin`, `~/.bun/bin`

덕분에 winget/npm으로 방금 설치한 CLI를 **재부팅 없이** 곧바로 쓸 수 있다.

## 자주 나오는 증상

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| `Claude Code: Not logged in · Please run /login` | 설치만 됨 | 「에이전트 설정」 → 로그인 |
| 상태가 `요금제 필요` | 무료 계정으로 로그인됨 | 「무료 CLI 자동 설치」 |
| `credit balance is too low` / `402` | 크레딧 소진 | 위와 동일 |
| 백엔드 목록이 「자동 할당 (로그인 필요)」 | 쓸 수 있는 CLI 0개 | 로그인 또는 무료 CLI 설치 |
| `gemini: npm 없음` | Node.js 미설치 | 「무료 CLI 자동 설치」가 Node부터 깐다 |
| 토론이 한 발언에서 오래 멈춤 | CLI 응답 지연 | `ARENATALK_CLI_TIMEOUT=180` 로 대기 시간 조절 |

## 환경변수

| 변수 | 뜻 |
| --- | --- |
| `ARENATALK_CLI_TIMEOUT` | CLI 한 턴 대기 시간(초). 기본 600 |
| `ARENATALK_MODEL_<NAME>` | 해당 CLI에 넘길 모델 id 고정 (예: `ARENATALK_MODEL_CLAUDE=opus`) |
