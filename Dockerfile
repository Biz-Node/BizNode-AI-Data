# ARM(t4g) 서버용 — `python:3.12-slim` 은 arm64 를 지원한다.
FROM python:3.12-slim

WORKDIR /app

# 의존성을 먼저 넣어야 코드만 바뀔 때 이 층이 캐시된다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY pipeline/ pipeline/
COPY batch/ batch/

# 배치가 읽는 시드 목록·설정
COPY data/ data/

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Seoul

EXPOSE 8100
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8100"]
