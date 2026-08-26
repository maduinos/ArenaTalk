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

## Ubuntu `.deb` 설치

일반 사용자는 PyInstaller one-folder를 담은 `.deb`를 설치합니다 (Python/venv 불필요).

```bash
sudo apt install ./arenatalk_0.0.2_amd64.deb
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
# → dist/ubuntu/arenatalk_0.0.2_amd64.deb
```

## 소스에서 실행

```bash
pip install -e '.[gui]'
arenatalk
arenatalk list
arenatalk debate '주제' --backend mock
arenatalk setup
```

## 개발

```bash
pip install -e '.[dev,gui]'
pytest
```
