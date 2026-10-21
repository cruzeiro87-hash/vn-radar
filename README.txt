베트남 영업 레이더 - 클라우드(GitHub) 설정 방법
※ 이 폴더는 GitHub에 올리는 용도입니다. PC 위젯 폴더(vn_radar)와는 별개입니다.

[1] GitHub 가입
  https://github.com/signup  (cruzeiro87@gmail.com 으로 가입, 무료)

[2] 저장소 만들기
  오른쪽 위 + → New repository
  - Repository name: vn-radar
  - Public 선택 (무료로 매시간 실행·웹 주소 사용 조건)
  - Create repository

[3] 파일 올리기
  저장소 화면의 "uploading an existing file" 클릭
  → 이 폴더 안의 파일·폴더를 전부 끌어다 놓기
    (news_widget.py, widget_template.html, config.json, web 폴더, .github 폴더)
  → 아래 Commit changes 클릭
  ※ 올린 뒤 목록에 .github 폴더가 안 보이면:
    Add file → Create new file → 파일 이름에  .github/workflows/collect.yml  입력
    → collect.yml.txt 내용을 그대로 붙여넣고 Commit

[4] 웹 주소(Pages) 켜기
  Settings → Pages → Build and deployment → Source: "GitHub Actions" 선택

[5] (선택) 번역 예비용 이메일 등록
  Settings → Secrets and variables → Actions → New repository secret
  - Name: MYMEMORY_EMAIL
  - Secret: cruzeiro87@gmail.com

[6] 첫 수집 실행
  Actions 탭 → (안내가 뜨면 "I understand..." 눌러 활성화)
  → 왼쪽 "뉴스 수집" → Run workflow
  첫 실행은 한국어·영어 번역 때문에 5~10분 걸립니다. 초록색 체크가 뜨면 완료.
  이후에는 매시간 자동으로 실행됩니다.

[7] 내 주소
  https://(GitHub 아이디).github.io/vn-radar/

[8] 휴대폰에 설치
  크롬으로 위 주소 열기 → 오른쪽 위 ⋮ → "앱 설치" (또는 "홈 화면에 추가")
  → 홈 화면에 앱 아이콘이 생기고 앱처럼 전체 화면으로 열립니다.

[9] PC 위젯도 같은 화면으로
  PC의 vn_radar\config.json 에서
    "cloud_url": "https://(GitHub 아이디).github.io/vn-radar/",
  로 저장 → widget.bat 실행
  ※ 회사 네트워크가 github.io를 막으면 화면이 안 뜹니다. 그때는 cloud_url을 다시 ""로 비우면
    기존처럼 PC에서 직접 수집합니다.

알아둘 점
- 화면 설정(테마·언어·글자 크기)과 체크 목록은 기기마다 따로 저장됩니다.
- GitHub가 바쁠 때는 매시간 실행이 몇 분~수십 분 늦어질 수 있습니다.
- 저장소에 60일 동안 아무 변경이 없으면 GitHub가 자동 실행을 멈추고 이메일을 보냅니다.
  메일의 안내대로 Actions에서 다시 켜거나, 가끔 config.json 검색어를 고쳐 저장하면 됩니다.
- 검색어·관심기업 수정: GitHub에서 config.json 열기 → 연필 아이콘 → 수정 → Commit
