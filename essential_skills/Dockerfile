FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY reference_search.py chemical_names.json tools.py gateway.py worker.py server.py http_server.py reference-warnings.json ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ESSENTIAL_TOOLS_BIND=0.0.0.0 ESSENTIAL_TOOLS_ENV=production
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()"]
ENTRYPOINT ["python", "-B", "http_server.py"]
