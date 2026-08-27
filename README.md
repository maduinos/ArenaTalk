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
sudo apt install ./arenatalk_0.0.4_amd64.deb
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
# → dist/ubuntu/arenatalk_0.0.4_amd64.deb
```

## 소스에서 실행

```bash
pip install -e '.[gui]'
arenatalk
arenatalk list
arenatalk debate '주제' --backend mock
arenatalk setup
arenatalk login --status
```

## 개발

```bash
pip install -e '.[dev,gui]'
pytest
```
