# Netlify Upload Folder

업로드 대상: 이 폴더(`deploy/netlify`) 전체

포함 내용
- `portfolio/index.html`: 정적 포트폴리오 페이지
- `static/`: 공통 CSS/JS/이미지
- `netlify.toml`: Cloudtype 백엔드 프록시 설정

설정 필요
1. `netlify.toml`의 `YOUR-CLOUDTYPE-DOMAIN`을 실제 Cloudtype 도메인으로 변경
2. Netlify Publish directory를 `deploy/netlify`로 지정

접속 주소(예시)
- 포트폴리오: `https://<netlify-domain>/portfolio/`
- 과외: `https://<netlify-domain>/tutoring/` (Cloudtype로 프록시)
- 관리자: `https://<netlify-domain>/admin/login` (Cloudtype로 프록시)
