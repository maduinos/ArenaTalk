# Ubuntu 패키징과 배포

ArenaTalk의 공식 배포·검증 대상은 Ubuntu Desktop 22.04/24.04 LTS amd64이다.
일반 사용자에게는 PyInstaller one-folder 런타임을 담은 `.deb`를 제공하며,
Python, venv, pip을 별도로 설치하게 하지 않는다.

## 릴리스 빌드

릴리스 산출물은 **Ubuntu 22.04 LTS amd64** 호스트에서 빌드한다. PyInstaller의
Linux 산출물은 빌드 호스트의 glibc에 영향을 받으므로 24.04에서 릴리스를 만들면
22.04 호환성을 보장할 수 없다.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[gui,packaging,dev]"
tools/build_ubuntu_deb.sh
```

기본 출력은 `dist/ubuntu/arenatalk_0.0.5_amd64.deb`이다. 다른 경로는
`tools/build_ubuntu_deb.sh --output-dir DIRECTORY`를 사용한다.

패키지 구성:

- `/usr/lib/arenatalk/`: PyInstaller one-folder 런타임
- `/usr/bin/arenatalk`: CLI·GUI 진입점 (인자 없으면 GUI)
- `/usr/share/applications/arenatalk.desktop`
- `/usr/share/pixmaps/arenatalk.png`
- `/usr/share/doc/arenatalk/`: README와 이 문서

패키지에는 maintainer script(`postinst` 등)를 두지 않는다. 캐릭터 자산과
에이전트 CLI는 `.deb`에 포함하지 않는다.

## 산출물 검사

```bash
dpkg-deb --info dist/ubuntu/arenatalk_0.0.5_amd64.deb
dpkg-deb --contents dist/ubuntu/arenatalk_0.0.5_amd64.deb
sha256sum dist/ubuntu/arenatalk_0.0.5_amd64.deb
```

## 설치와 첫 실행

```bash
sudo apt install ./dist/ubuntu/arenatalk_0.0.5_amd64.deb
arenatalk characters root --set ~/path/to/CharacterPet/characters
arenatalk
```

캐릭터 폴더는 AgentPet과 공유한다 (`~/.config/agentpet/state.json`의
`characters.path`). AgentPet이 설치돼 있으면 GUI 「폴더 선택」 또는
`agentpet character root --set`으로도 같은 경로를 설정할 수 있다.

에이전트 CLI(Codex / Claude / Cursor / gemini 등)는 패키지 밖이다.
`arenatalk setup`으로 감지·선택 설치하거나, mock 백엔드로 실행할 수 있다.

## 업데이트

APT repository가 없는 동안 새 `.deb`를 다시 설치한다.

```bash
sudo apt install ./arenatalk_0.0.5_amd64.deb
```

사용자 Elo DB(`~/.local/share/arenatalk/`)와 캐릭터 폴더 설정은 패키지 교체로
지우지 않는다.
