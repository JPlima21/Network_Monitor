FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

EXPOSE 3000

VOLUME ["/app/data"]

CMD ["python", "-c", "from backend.server import run; run(host='0.0.0.0', port=3000)"]
