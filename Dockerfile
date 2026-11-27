FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MINERVA_DADOS=/data MINERVA_TORRENTS=/data/torrents TZ=America/Sao_Paulo
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests

RUN useradd -u 1000 -m minerva && mkdir -p /data && chown minerva /data
USER minerva
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/saude',timeout=4)"
# MINERVA_PROXY_IPS = IP do proxy reverso (Caddy/nginx) que manda X-Forwarded-Proto; padrão: nenhum além do localhost
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${MINERVA_PROXY_IPS:-127.0.0.1}\""]
