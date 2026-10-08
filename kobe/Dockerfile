FROM python:3.11-slim

WORKDIR /app

# kobe フォルダ内にあるファイルを指定してコピー
COPY kobe/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションのコードをコピー
COPY kobe/ .

# ポートを指定してUvicornで起動
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]