# Cloudtype Upload Folder

업로드 대상: 이 폴더(`dist/cloudtype`) 전체

포함 내용
- `app.py`
- `requirements.txt`
- `Procfile`
- `templates/`
- `static/`

실행
- Procfile 기준으로 `gunicorn app:app` 실행
- 앱은 `PORT` 환경변수를 자동 사용하도록 설정됨

권장 환경변수
- `SECRET_KEY`: 세션 암호화 키
- `CORS_ALLOW_ORIGIN`: Netlify 도메인 (예: `https://your-site.netlify.app`)

기본 API
- `GET /healthz`
- `GET /api/healthz`
- `GET /api/portfolio`
- `GET /api/notices/public`
