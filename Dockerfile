FROM python:3.11-slim

WORKDIR /workspace

COPY kobe/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
CMD exec uvicorn kobe.main:app --host 0.0.0.0 --port $PORT