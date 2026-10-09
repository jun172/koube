FROM python:3.11-slim

WORKDIR /app

# 依存関係ファイルをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションのコードをすべてコピー
COPY . .

# ポートを指定してUvicornで起動
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]