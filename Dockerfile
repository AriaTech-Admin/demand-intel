FROM python:3.11-slim

WORKDIR /app

# System deps for pandas/numpy if needed
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render provides PORT env var; default 8000 for local
ENV PORT=8000
ENV DB_PATH=/tmp/demand_intel.db

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
