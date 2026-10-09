FROM python:3.11-slim

WORKDIR /app

# Build context is the kobe/ directory (Railway root directory = /kobe)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
