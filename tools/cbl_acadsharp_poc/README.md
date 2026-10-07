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

## 코드 페이지와 다른 UTF-8 문자열 (`acadsharp-misdeclared-utf8.patch`)

예전 ChickenBananaCAD 저장 경로는 코드 페이지가 KS C 5601(ANSI_949)인 2007 이전 DWG에
문자열을 UTF-8 바이트로 썼다(S-501 (2차보완)-29~32 등). 코드 페이지로 읽으면 "湲곗큹 F1"처럼
깨지고, 저장할 때마다 그중 일부가 "?"가 되어 되살릴 수 없었다. 이 패치는 바이트가 올바른 UTF-8이고
풀었을 때 한글 음절이 나오면 UTF-8로 읽는다(KS C 5601 한글은 사실상 올바른 UTF-8이 되지 않는다).
그런 문자열이 하나라도 있는 도면은 `DwgReader.PreferMisdeclaredUtf8`로 한 번 더 읽어 올바른 UTF-8
문자열을 모두 UTF-8로 읽는다("900×400"의 UTF-8 C3 97은 KS C 5601로도 "횞"라는 글자라 문자열 하나만
보고는 가릴 수 없다). 이미 바이트 일부가 "?"로 바뀐 문자열은 되살리지 못하고 예전처럼 읽는다.
`DwgReader.MisdeclaredUtf8Strings`가 그 수를 세고, `Program.cs`가 "Misdeclared UTF-8 strings read: N"
알림을 남기며, 서버 열기 API가 `misdeclared_utf8_texts`로 돌려준다. 저장하면 코드 페이지로 바로 쓴다.

## R2013+ 도면의 REGION (`acadsharp-region-sab.patch`)

AutoCAD 2013 이후 형식(ZWCAD 포함)은 REGION/3DSOLID의 ACIS 데이터를 엔티티가 아니라 AcDs
데이터 영역에 이진(SAB, "ACIS BinaryFile" 또는 "ASM BinaryFile4")으로 둔다. ACadSharp는 그 영역을
읽기만 하고 엔티티에 붙이지 않아, AC1018 저장이 "has no ACIS payload"로 거절됐다(사무동 1~5층).
`Program.cs`의 `AttachStoredAcis`가 저장할 때 SAB를 엔티티에 붙이고(다른 형식이면 붙이지 않아 예전처럼
거절), 이 패치는 그 SAB를 AC1018 엔티티의 version 2 데이터로 그대로 쓴다. 사무동 도면과
`core/test_fixtures/cad/region_acds_ac1032.dwg`에서 ODA(검토용)가 원본과 저장본의 REGION을 같은
데이터로 읽었다. 3DSOLID/BODY는 ACadSharp writer가 원래 쓰지 않아 저장 검증이 거절한다(변경 없음).


## 쓰기 속도 (`acadsharp-writer-lookups.patch`)와 저장 한 번의 변환기 실행

ACadSharp DWG writer는 블록 정의마다 모델 공간의 모든 객체를 두 번 훑어 그 블록의 INSERT를 찾았고
(블록 수 × 객체 수), DXF 클래스마다 문서의 모든 객체를 다시 셌다(클래스 수 × 객체 수). 치수마다
블록이 하나씩 생기므로, 치수 1,500개·객체 59,000개 합성 도면은 쓰기만 3.9초가 걸렸다. 이 패치는 둘 다
쓰기 한 번에 한 번만 만든다(INSERT 순서와 출력 바이트는 같다).

`Program.cs` writer는 `--reread-metadata <path>`를 받으면 저장본을 다시 읽은 그 결과로 `--metadata`와
똑같은 보고서를 남기고(서버가 저장본을 다시 읽으려 프로세스를 또 띄우지 않게), 편집한 원본 문서를 놓아준
뒤 저장본을 읽는다(두 도면이 동시에 메모리에 있지 않게). 서버 저장은 원본 메타데이터(편집 대상 확인용)를
검증에도 다시 쓰므로, dwgread가 없는 운영 서버에서 변환기를 4번이 아니라 2번 실행한다.
S-501-16(6.1MB): 맥 기준 저장 11.7초 → 7.2초, writer 최대 메모리 720MB → 449MB.

## 표에 없는 이름을 가리키는 DXF 헤더 (`Program.cs` `FixMissingHeaderReferences`)

ezdxf가 만든 DXF는 `$DIMSTYLE`이 표에 없는 "ISO-25"를 가리키고, 다른 프로그램도 지운 레이어·스타일 이름을
`$CLAYER`/`$CELTYPE`/`$TEXTSTYLE`/`$DIMTXSTY`/`$CMLSTYLE`에 남긴다. ACadSharp DWG writer가 그 이름으로 표를
찾다가 KeyNotFoundException을 던져 DXF 열기 전체가 거절됐다. `--dwg-from-dxf`는 읽은 뒤 없는 이름을 기본
항목(0, ByLayer, Standard)으로 바꾸고 "Header $X names a missing entry" 알림을 남긴다(AutoCAD도 열 때 이렇게
바로잡는다).

## AC1018 다중 지시선 (`acadsharp-mleader-ac1018.patch`)

ACadSharp는 2007 이전 형식 MLEADER에서 읽기가 기대하는 화살촉 개수(BL)를 쓰지 않았고("R2007pre not supported"),
문자가 없는 문맥에서 "블록 내용 있음" 비트를 블록이 있을 때만 썼다. 저장본을 다시 읽으면 MLEADER가 어긋나 사라져
(ODA는 파일 전체를 "Object improperly read: AcDbMLeader"로 거절) 다중 지시선이 있는 도면은 저장이 거절됐다.
화살촉 목록은 읽을 때 보관되지 않으므로 0개를 쓴다(기본 화살촉 342는 그대로). `core/test_fixtures/cad/mleader_ac1032.dwg`
(ezdxf + ODA, 문자형·블록형 하나씩)로 저장·재저장·ODA 재판독을 확인한다.

## 이동할 수 있는 객체 (`Program.cs` `MoveEntity`)

편집기는 SPLINE·ELLIPSE·SOLID·3DFACE·POINT·LEADER·HATCH·DIMENSION·MULTILEADER를 그리지만 다시 만들지는 못하므로,
이동만 `move` op로 보낸다(다른 편집은 편집기가 저장 전에 거절). writer는 점만 옮긴다: ACadSharp의 ApplyTransform은
방향 벡터(스플라인 접선, 타원 짧은 축, 해치 무늬 축)까지 평행이동하고 MULTILEADER에서는 아무것도 하지 않기 때문이다.
해치는 경계 요소·씨앗점·무늬 선 기준점을, 치수는 모든 정의점(XYZ 속성, Normal 제외)과 치수 블록의 객체를,
MULTILEADER는 문맥 데이터(내용 기준점, 문자 위치, 지시선 꼭짓점·연결점, 블록 위치와 변환 행렬)를 옮긴다.
`core/test_fixtures/cad/move_kinds_ac1032.dwg`(ezdxf + ODA)로 종류마다 확인하고 ODA(검토용)로 다시 읽는다.

## 회전·크기·대칭과 복사 (`Program.cs` `TransformEntity`, `CopyEntity`)

같은 종류들의 회전·균일 크기 변경·대칭은 `transform` op(`matrix` [a,b,c,d,e,f]: x' = a·x + c·y + e,
y' = b·x + d·y + f, 닮음 변환만, 비균일 크기·기울임은 거절)로, 복사는 `add_copy` op(`copyOf` 원본 handle과 같은
행렬)로 보낸다. 복사본은 원본 객체를 복제한 뒤 변환하므로 종류가 그대로다(해치는 채우기, 치수는 자기 익명 블록 `*D<n>`,
지시선은 꼭짓점 목록을 따로 가진다). 점은 행렬로, 방향 벡터(스플라인 접선, 타원 장축, 해치 무늬 오프셋)는 선형 부분만으로
옮긴다. 대칭은 호의 시작·끝을 바꾸고 볼록 값(bulge)의 부호를 뒤집는다. 해치 경계의 시계 방향 원호·타원호는 각도를 음수로
저장하므로(AutoCAD 규칙, ACadSharp 자체 변환도 같다) 실제 각도로 바꿔 돌린 뒤 다시 저장한다. 무늬 해치는 무늬도 함께 대칭한다
(선 묶음마다 각도 반전, 기준점·간격 대칭; 편집기 화면과 같다). 각도·배율은 setter가 무늬 선을 다시 만들므로 필드로 직접 바꾼다.
편집기는 해치 경계의 타원호(시계 방향은 음수 각도)와 스플라인 요소(맞춤점 수도 그룹 97을 쓴다)도 그린다.

치수의 크기 변경은 AutoCAD가 치수를 다시 만들 때처럼 새 길이를 글자에 쓴다: 블록 글자에서 예전 측정값인 숫자(스타일
서식 그대로, 또는 도면의 자릿수·천 단위 쉼표)를 찾아 새 값으로 바꾼다. `<>` 없는 재지정 글자는 그대로, 각도 치수는 값이
변하지 않는다. ACadSharp의 `IsAngular`는 종류를 비트로 읽어 세로좌표·지름 치수도 각도로 보므로 각도 치수는 클래스로 고른다.
치수 대칭은 정의점을 대칭하고 회전 치수의 각도를 대칭한 각으로 바꾼다. 글자는 읽을 수 있는 방향(−90°보다 크고 90° 이하)으로
둔다(MIRRTEXT 0, 편집기 화면과 같다). 세로좌표 치수는 원점 기준 축 방향 거리라 회전·대칭하면 값이 틀리므로 거절한다.

다중 지시선의 좌우 대칭은 글자를 읽을 수 있게 두고(MIRRTEXT 0) 방향을 유지한 채 지시선 반대쪽에 놓는다(붙는 점·줄 정렬
왼쪽↔오른쪽). 위아래·기울어진 축 대칭은 편집기가 화면의 글자 자리와 방향(`textLocation`, `textRotation`)을 함께 보내고
writer가 그 자리에 놓는다(없으면 거절). 블록 내용 다중 지시선은 이동·회전·크기 변경 때 블록 위치와 변환 행렬도 함께
바꾼다(대칭은 거절). 이동 성분이 없는 변환 행렬(ezdxf가 만든 파일)은 위치를 담지 않는 것으로 보고 블록 위치를 따로 옮긴다.

속성 있는 INSERT를 돌리거나 크기·대칭을 바꾸면 편집기가 화면의 속성 위치를 `attributes`(insert, rotation, height)로
보내고, writer가 그 자리에 놓는다(handle, 복사본은 원본 속성 handle, 없으면 유일한 tag로 찾는다; 못 찾으면 거절).
정렬된 TEXT·ATTRIB는 정렬점(DXF 11)을 글자와 함께 돌리고 키운다. MTEXT의 회전은 x축 방향(DXF 11)으로 쓴다.
편집한 속성 값은 같은 `attributes` 항목의 `text`로 보낸다(블록이 그대로여도 블록 수정과 함께; 여러 줄 속성의 값 수정은
거절). TEXT·MTEXT 수정은 편집기가 글자 내용과 높이를 바꿨을 때만 `text`·`height`를 보내므로, 옮기기·회전만 한 여러 줄
MTEXT는 줄바꿈과 서식을 그대로 지킨다. 대칭한 TEXT·MTEXT는 읽을 수 있는 방향으로 둔다. 편집기는 MTEXT의 줄(`\P`, 그룹 3
조각)을 붙는 점 기준으로 줄마다 그리고, 내용을 고친 MTEXT는 줄바꿈을 `\P`로 쓴다. 옮기거나 크기만 바꾼 MTEXT 복사본은
`add_copy`로 보내 줄·붙는 점·서식을 지킨다(내용을 고치거나 돌리거나 대칭한 복사본은 화면 자리에 한 줄 TEXT로 추가).

결과가 틀리게 보일 변환은 한국어로 거절한다: 세로좌표 치수의 회전·대칭, 글자 자리 없이 보낸 다중 지시선의 위아래·기울어진
대칭, 블록 내용 다중 지시선의 대칭. 편집기는 복사(`add_copy`, 복사한 INSERT)를 원본 편집보다 먼저 보내므로 같은 저장에서 원본을 옮기거나 지워도 복사본은
원본의 저장 전 모습에서 만들어진다. `core/test_cad_dwg_transform_kinds.py`, `core/test_cad_dwg_attrib_transform.py`가
종류마다 확인하고 ODA(검토용)로 다시 읽는다.

## 아래를 향하거나 기울어진 객체 (OCS, `Program.cs` `ToOcs`)

ARC·CIRCLE·LWPOLYLINE·TEXT(ATTRIB)·INSERT·SOLID·HATCH는 점을 자기 좌표계(OCS)에 둔다. OCS는 법선(DXF 210/220/230)에서
AutoCAD의 임의 축 규칙으로 정해지고, 법선이 (0, 0, −1)이면(AutoCAD 3D 대칭 등) x축이 월드 −x다. 편집기는 이런 객체를 월드
좌표로 그리고 보낸다(블록 행렬에 OCS 행렬을 곱해 그린다; 반전 행렬의 호는 끝점 방향을 따라가며 시작·끝을 바꾼다). writer는
월드 값을 OCS로 되돌려 쓴다: 이동량은 OCS로 바꿔 더하고, 수정(`update`)은 x 부호·호 각도(π − 끝 ~ π − 시작)·볼록 값 부호·
블록 회전(−r)·x 배율(−x)을 바꾸고, 회전·크기·대칭은 행렬을 x 반전으로 감싸(F·M·F) OCS에 적용한다. 속성은 속성마다 자기
OCS로 옮긴다. 타원은 중심·장축이 월드 값이라 그대로 두고 짧은 축 방향만 법선을 따른다. 법선이 기울어진 객체(3D)는 위에서 본
투영으로 그리고(호·원은 점으로), 복사·이동은 정확히 옮기지만 수정·회전·크기·대칭은 한국어로 거절한다. 평면 객체의 값은
비트 단위로 그대로다. `core/test_cad_dwg_ocs_extrusion.py`(ezdxf + `--dwg-from-dxf`로 만든
`ocs_extrusion_ac1018.dwg`)와 `core/test_cad_ocs_import.py`(편집기 계산, node).

## 되돌린 객체 (`--restore-from`, `fromOpened`)

앞선 저장에서 지운 객체를 되돌리기로 살리면 그 객체는 DWG에 없다. 편집기는 처음 연 도면(바이트와 기준 도형)을 들고 있다가
그런 객체를 `fromOpened` 복사로 보내고 처음 연 도면을 `restore_dwg`로 함께 올린다. 서버는 그 파일을 `--restore-from`으로
넘기고, writer는 거기서 객체를 복제한다(종류·값·속성 글자 유지; 레이어·스타일은 이름으로 대상 도면에 연결). 검증은 처음 연
도면을 읽은 결과로 종류 수를 맞춘다. `core/test_cad_dwg_restore.py`.
