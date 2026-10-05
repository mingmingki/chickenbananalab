# ACadSharp DWG save POC

격리된 저장 가능성 시험이다. ChickenBananaCAD 운영 코드, 저장 endpoint, 서버,
ODA 경로와 연결하지 않는다. 원본 DWG와 LibreDWG writer는 수정하지 않는다.

## 실행

```sh
DOTNET_CLI_HOME=/private/tmp/dotnet-cli-home \
  /private/tmp/dotnet-acadsharp/dotnet run --project CblAcadSharpPoc.csproj -- \
  input.dwg /private/tmp/acadsharp-save-test/output_ACADSHARP_AC1018.dwg AC1018
```

`AC1018`은 ACadSharp의 AutoCAD 2004 계열 출력 버전이다. CLI는 입력·출력 경로가
같으면 거부하고, 출력 임시 파일 작성 → ACadSharp 재판독 → `File.Move` 순서로
Save As를 수행한다. 출력 lock, 900초 대기 제한, 빈/비정상 파일 거부, 실패 임시
파일 삭제, 입력 SHA-256 기록을 포함한다.

실제 5개 도면의 독립 검증은 `/private/tmp/acadsharp-save-test/`에 보관한다.
`dwgread`는 저장하지 않고 저장본 재판독 비교에만 사용했다.

## Local Save As integration

The project-local Django integration is disabled unless
`CBLCAD_FREE_DWG_LOCAL=1` is explicitly set. It uses the fixed local runtime
at `tools/cbl_acadsharp_poc/runtime/CblAcadSharpPoc`; it does not download or
build per request and never falls back to ODA. Rebuild it with:

```sh
ACADSHARP_SOURCE_ROOT=/private/tmp/ACadSharp-3.6.51-poc \
  ./tools/cbl_acadsharp_poc/build_runtime.sh
```

Reproducible setup (2026-09-30 rebuild; `/private/tmp` is wiped on reboot, so
keep the SDK and fork outside it):

```sh
# .NET 9 SDK, user-local (no sudo)
curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh
bash dotnet-install.sh --channel 9.0 --install-dir "$HOME/.dotnet" --no-path

# ACadSharp v3.6.51 + this repo's REGION/MINSERT patch
git clone https://github.com/DomCR/ACadSharp.git ~/chickenbanana-work/_build/ACadSharp-3.6.51-cbl
git -C ~/chickenbanana-work/_build/ACadSharp-3.6.51-cbl checkout 219e5fc4a6def2b2d22fbbc1c2597d8e588df6c8
git -C ~/chickenbanana-work/_build/ACadSharp-3.6.51-cbl submodule update --init --recursive
./tools/cbl_acadsharp_poc/apply_acadsharp_region_minsert_patch.sh ~/chickenbanana-work/_build/ACadSharp-3.6.51-cbl
```

Build from a clean copy of `CblAcadSharpPoc.csproj` + `Program.cs`: stale
`obj/` output in this folder is compiled in again and fails with duplicate
assembly attributes. After a rebuild, compare old vs new runtime output on
real drawings (`--metadata`, `--dxf` and an empty-ops save) before committing.

### 런타임 파일은 git이 아니라 GitHub Release에 둔다

`runtime/*/CblAcadSharpPoc.bin`(각 약 70MB)은 git에 넣지 않는다(2026-10-05까지
빌드할 때마다 저장소가 약 150MB씩 커졌다). `runtime/RUNTIME.json`이 릴리스 이름과
플랫폼별 SHA-256을 적고, `fetch_runtime.py`가 체크섬이 다른 파일만 내려받아 검증한 뒤
바꾼다(다르면 설치된 파일을 그대로 둔다).

```sh
./tools/cbl_acadsharp_poc/fetch_runtime.py            # 이 컴퓨터용
./tools/cbl_acadsharp_poc/fetch_runtime.py all        # 두 플랫폼 모두
```

새 런타임을 내보낼 때: 두 플랫폼을 빌드해 `runtime/*/`에 넣고 비교한 뒤,

```sh
cp runtime/linux-x64/CblAcadSharpPoc.bin /tmp/CblAcadSharpPoc-linux-x64.bin
cp runtime/macos-arm64/CblAcadSharpPoc.bin /tmp/CblAcadSharpPoc-macos-arm64.bin
gh release create acadsharp-runtime-YYYYMMDD /tmp/CblAcadSharpPoc-*.bin --title ... --notes ...
```

`RUNTIME.json`의 release와 sha256을 고쳐 커밋한다(`core/test_acadsharp_runtime_manifest.py`가
설치된 파일과 목록이 다르면 실패한다). 서버 배포는 `git merge` 다음에
`tools/cbl_acadsharp_poc/fetch_runtime.py linux-x64`를 실행한다.

The Save As endpoint is `/api/cblcad/free-dwg-save/`. It accepts an uploaded
original DWG and a JSON `ops` array, writes a temporary AC1018 file, rereads
it with LibreDWG, and returns it only after REGION/MINSERT validation.

## 라이선스와 API 기준

- ACadSharp 공식 `v3.6.51` source ProjectReference, commit
  `219e5fc4a6def2b2d22fbbc1c2597d8e588df6c8`, MIT. 원문은
  `ThirdParty/ACadSharp-LICENSE.txt`.
- 공식 호환표상 DWG writer는 AC1014, AC1015, AC1018, AC1024, AC1027, AC1032를
  지원하며 AC1021은 지원하지 않는다.
- 이 POC의 대상은 AC1018 하나이며, 지원되지 않은 버전을 강제로 지정하지 않는다.

## 판정 원칙

ACadSharp 자기 재판독만으로 성공 처리하지 않는다. 저장본은 LibreDWG `dwgread`
JSON과 기존 격리 무료 compact 열기 경로로도 재판독한다. REGION·문자·블록·형상
손실이 하나라도 확인되면 기존 저장 버튼에 연결하지 않는다.

## REGION fork POC

`/private/tmp/ACadSharp-3.6.51-poc`의 격리 fork에서만 REGION writer를 시험한다.
AC1018 version 2 modeler payload에 대해 reader가 원본 raw ACIS bytes와 block/version을
보존하고, writer가 그 raw payload를 같은 block 구조로 기록한다. payload가 없으면
writer는 실패하며 Region을 조용히 생략하지 않는다. 이 fork는 프로젝트 밖에 있고
커밋·push되지 않는다.

MINSERT는 실제 DWG object type이 MINSERT일 때만 `WasReadAsMInsert`를 설정한다.
따라서 1x1 MINSERT의 배열 필드·spacing을 보존하고, 새 1x1 Insert는 일반 INSERT로
남는다. 재현용 패치는 `acadsharp-region-minsert.patch`, 적용 스크립트는
`apply_acadsharp_region_minsert_patch.sh`이다.

## 코드페이지 밖 문자 (`acadsharp-unicode-escape.patch`)

AC1018 문자열은 도면 코드페이지로 저장된다. ANSI_1252 도면의 한글처럼 코드페이지에
없는 문자는 원래 `?`로 바뀌었다. 이 패치는 AutoCAD와 같이 `\U+XXXX`로 쓰고, DWG를
읽을 때 다시 문자로 푼다(DXF는 서버 열기 API가 푼다). XRECORD·확장데이터 문자열은
길이를 글자 수로 적으므로 예전 인코딩(`?`)을 그대로 쓴다. U+FFFD(읽기 실패 표시)와
BMP 밖 문자도 `?`로 남는다. 같은 적용 스크립트가 패치들을 차례로 적용한다.

## XRECORD·확장데이터 문자열 (`acadsharp-xrecord-text.patch`)

AC1018의 XRECORD·확장데이터 문자열은 길이(바이트 수)와 코드페이지 순번을 함께
저장한다. 원래 reader는 순번(예: 40)을 Windows 코드페이지 번호로 읽어 한글을
UTF-8로 풀어 깨뜨렸고, writer는 길이를 글자 수로 적었다. 이 패치는 순번을
실제 코드페이지로 바꿔 읽고(모르는 순번은 예전 동작), 길이를 바이트 수로 쓴다.

## 높이 0인 TEXT (`acadsharp-text-height-zero.patch`)

AutoCAD는 높이 0인 TEXT(문자 스타일 높이를 따름)를 저장한다. ACadSharp의 `Height`
setter는 0을 거부해서 DWG reader가 그 객체를 "Could not read TEXT"로 버렸고, 저장할
때마다 사라졌다. 이 패치는 reader가 저장된 높이를 그대로 넣는다. 그래도 읽지 못한
객체가 남으면 서버가 저장을 거부하고 편집기가 열 때 알린다.

## SORTENTSTABLE 사전 키 (`acadsharp-sortents-key.patch`)

SORTENTSTABLE은 자기 이름 없이 사전에 등록된 키만 갖는데, ACadSharp 생성자가 이름을
"ACAD_SORTENTS"로 정해 두어 reader가 그 이름으로 사전에 넣었다. DGN을 거친 도면은
ACAD_DGNLINESTYLECOMP에 블록 없는 SORTENTSTABLE 수천 개를 각자의 키로 두는데,
첫 개만 잘못된 키로 남고 나머지는 저장할 때 사라졌다("Error when trying to add the
entry ACAD_SORTENTS"). 이 패치는 파일에 적힌 키를 쓴다. 블록의 그리기 순서
(블록 확장 사전의 ACAD_SORTENTS)는 원래 보존됐다.

## 글자 수로 적힌 문자열 길이 (`acadsharp-legacy-text-lengths.patch`)

2026-10-01 이전 writer는 XRECORD·확장데이터 문자열 길이를 글자 수로 적었다. 한글은
두 바이트라 그 뒤 데이터가 어긋나서, AutoCAD·ODA는 복구 없이는 파일을 열지 못하고
reader는 문자열을 잘라 읽었다("Unknown code for extended data"). 이 패치는 2007 이전
도면에서 데이터가 깨끗하게 읽히지 않을 때만 길이를 글자 수로 보고 다시 읽고, 끝까지
정확히 맞으면 그 결과를 쓰며 "Legacy character-count string lengths read in ..." 알림을
남긴다. 서버 열기 API가 그 수를 `legacy_text_lengths`로 돌려주고 편집기가 저장하면
바로잡힌다고 알린다. 저장할 때는 바이트 수로 적는다.

## DXF 코드 페이지 ANSI_949 (`acadsharp-dxf-codepage-ansi949.patch`)

한국어 AutoCAD는 DXF의 `$DWGCODEPAGE`를 ANSI_949로 적는데 ACadSharp의 이름 표에는
`kcs5601`만 있어서, 2007 이전 DXF의 한글을 Windows-1252로 읽어 깨뜨렸다. 이 패치는
ANSI_949를 KS C 5601로 읽는다. 편집기가 DXF를 열 때 서버가 `--dwg-from-dxf`로 먼저
AC1018 DWG를 만들기 때문에 필요하다.
