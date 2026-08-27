FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY asda /app/asda
COPY config /app/config
COPY sample_data /app/sample_data

RUN pip install --no-cache-dir ".[postgres,redis]"

EXPOSE 8080 8501
CMD ["sh", "-c", "uvicorn asda.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
