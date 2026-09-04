> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-08-26<br>
> https://maduinos.blogspot.com/

# ArenaTalk

CharacterPet 캐릭터가 주제를 토론하고 Elo로 순위를 매기는 데스크톱 아레나입니다.

## 캐릭터 폴더 (CharacterPet · AgentPet 공유)

설정은 **AgentPet 한곳**만 씁니다 (`~/.config/agentpet/state.json` → `characters.path`).

```bash
agentpet character root --set ~/path/to/CharacterPet/characters
# 또는 ArenaTalk GUI 「폴더 선택」 / CLI:
arenatalk characters root --set ~/path/to/CharacterPet/characters
```

레이아웃:

```text
<characters>/<id>/pet.json   # persona 포함
<characters>/<id>/spritesheet.webp
```

우선순위: `--characters` → `ARENATALK_CHARACTERS` → AgentPet `characters.path` → XDG 기본.

## 전문가 토론

주제와 관심사가 같은 단어를 쓰지 않으면 관심도 캐스팅이 무너집니다. "주식"으로
물으면 그 단어를 안 쓰는 캐릭터가 전부 바닥 점수로 동점이 돼서 출전이 사실상
무작위가 됩니다. GUI 오른쪽 **「전문가」 탭**은 다른 질문을 합니다 — 이미 그
분야 어휘를 가진 캐릭터가 누구인가.

분야를 고르면 점수 순으로 정렬되고 전문가가 미리 체크됩니다. 체크는 손으로
바꿀 수 있고, 「이 인원으로 토론 시작」을 누르면 그 인원만 출전합니다.

```bash
arenatalk experts                      # 분야 목록
arenatalk experts '주식·투자·경제'      # 이 분야 순위
arenatalk debate '주식 비중을 늘려야 하나' --domain '주식·투자·경제'
```

## 대기실 여론 조사 (토큰)

**기본값은 꺼짐입니다.** 켜면 출전하지 않은 캐릭터 전원이 본선과 동시에 한 번씩
투표합니다. 21명 로스터면 CLI 호출 18번이 본선 위에 얹히므로, 한 판에서 토큰을
가장 많이 쓰는 단계입니다.

GUI 툴바의 **「여론 · 켜기/끄기」** 로 켤 수 있습니다. 꺼진 상태에서는 본선 발언만
진행합니다. 승패·Elo는 원래 본선 발언으로만 정해지므로 결과 자체는 켜든 끄든
같고, 화면의 여론 칩과 `Lounge opinion` 집계만 달라집니다.

선택은 저장돼서 다음 실행에도 유지됩니다 (`~/.config/maduinos/ArenaTalk.ini`,
Windows는 `%APPDATA%\maduinos\ArenaTalk.ini`).

```bash
arenatalk debate '주제' --lounge      # 이 판만 켜기
arenatalk debate '주제' --no-lounge   # 이 판만 끄기 (기본값이라 대개 불필요)
export ARENATALK_LOUNGE=1             # 이 실행에서는 저장값보다 우선 (켬)
```

우선순위: `--no-lounge` → `--lounge` → `ARENATALK_LOUNGE` → GUI에 저장된 선택 → 끔.

## 에이전트 CLI (필수)

ArenaTalk은 설치된 에이전트 CLI를 불러 캐릭터를 말하게 합니다. **설치만으로는
부족하고 로그인까지 돼 있어야** 합니다 — 로그인하지 않은 `claude`가 깔려 있으면
예전에는 그 CLI가 선택돼서 모든 턴이 실패했습니다.

GUI 툴바의 **「에이전트 설정」** 에서 검사 · 무료 CLI 자동 설치 · 로그인을 모두
할 수 있습니다. 앱을 켰을 때 쓸 수 있는 CLI가 없으면 먼저 물어봅니다.

```bash
arenatalk backends        # 설치 + 로그인 상태
arenatalk login           # 로그인 안 된 CLI의 로그인 창 열기
arenatalk setup           # 없으면 무료 CLI(Node.js 포함) 자동 설치
```

Windows·macOS·Linux 모두 자동 설치를 지원합니다 (winget / Homebrew / npm).
자세한 내용: [`docs/AGENTS.md`](docs/AGENTS.md)

## Ubuntu `.deb` 설치

일반 사용자는 PyInstaller one-folder를 담은 `.deb`를 설치합니다 (Python/venv 불필요).

```bash
sudo apt install ./arenatalk_0.0.6_amd64.deb
arenatalk characters root --set ~/path/to/CharacterPet/characters
arenatalk
```

자세한 내용: [`docs/UBUNTU_PACKAGING.md`](docs/UBUNTU_PACKAGING.md)

### `.deb` 빌드 (22.04 amd64 권장)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e '.[gui,packaging,dev]'
tools/build_ubuntu_deb.sh
# → dist/ubuntu/arenatalk_0.0.6_amd64.deb
```

## 소스에서 실행

```bash
pip install -e '.[gui]'
arenatalk
arenatalk list
arenatalk debate '주제' --backend mock
arenatalk setup
arenatalk login --status
arenatalk experts
```

## 개발

```bash
pip install -e '.[dev,gui]'
pytest
```
