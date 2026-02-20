# Cloudtype Upload Folder

업로드 대상: 이 폴더(`deploy/cloudtype`) 전체

포함 내용
- `app.py`
- `requirements.txt`
- `Procfile`
- `templates/`
- `static/`

실행
- Procfile 기준으로 `gunicorn app:app` 실행
- 앱은 `PORT` 환경변수를 자동 사용하도록 설정됨

주의
- 세션 보안을 위해 Cloudtype 환경변수에 `SECRET_KEY` 설정 권장
- 데이터베이스는 기본 `portfolio.sqlite3`를 사용
