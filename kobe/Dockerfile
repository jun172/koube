# 軽量なPython公式イメージを使用
FROM python:3.12-slim

# 作業ディレクトリを指定
WORKDIR /app

# 依存関係ファイルを先にコピーしてインストール（キャッシュ効率化のため）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションのコードと静的ファイルをすべてコピー
COPY . .

# Cloud Runが割り当てるポート（環境変数 PORT）を受け取り、Uvicornで起動する設定
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"