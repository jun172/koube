FROM python:3.11-slim

WORKDIR /app

# システムの依存関係が必要な場合はここに追加（例: gccなど）
# RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# 依存関係ファイルをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションのコードをすべてコピー
COPY . .

# ポートを指定してUvicornで起動
# ※エントリーポイントとなるファイル名に合わせて "main:app" の部分を変更してください
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
