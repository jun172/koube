import os
from fastapi import APIRouter
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

router = APIRouter()  # ルートの定義

# .envファイルを読み込む
load_dotenv()

# 環境変数の取得（X API用のBearer Tokenなどを想定）
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabaseクライアントの初期化
# ※注意: Supabaseの初期化引数は通常 (URL, KEY) の順序になります
supabase: Client = create_client(str(SUPABASE_URL), str(SUPABASE_KEY))


async def get_and_send_to_supabase():
  if not X_BEARER_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    print("エラー: .envファイルに必要な設定が不足している")
    return

  # X API v2 の検索エンドポイント（例: 特定のキーワードやインフラ関連の投稿を検索する場合）
  # queryには検索したいキーワードを指定します（例: "インフラ 道路" など）
  query = "神戸市"
  url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&tweet.fields=created_at,public_metrics,author_id"

  headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}

  try:
    # X APIへのリクエスト
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    posts = data.get("data", [])

    for post in posts:
      # X APIから取得できるデータ構造に合わせて整形
      # public_metricsには retweet_count, reply_count, like_count, quote_count が含まれます
      metrics = post.get("public_metrics", {})

      insert_data = {
          "post_id": post.get("id"),
          "message": post.get("text"),
          "author_id": post.get("author_id"),
          "created_at": post.get("created_at"),
          "like_count": metrics.get("like_count", 0),
          "retweet_count": metrics.get("retweet_count", 0),
      }

      # Supabaseのテーブル（例: 'x_posts'）にインサート
      # supabase.table("x_posts").insert(insert_data).execute()
      print(f"取得・整形データ: {insert_data}")

  except Exception as e:
    print(f"X API リクエストエラー: {e}")