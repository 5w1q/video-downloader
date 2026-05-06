FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 给对方「试用期」镜像时：先生成 keys/trial_public.pem，再取消下一行注释
# COPY keys/trial_public.pem /app/keys/trial_public.pem

ENV PYTHONUNBUFFERED=1
EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
