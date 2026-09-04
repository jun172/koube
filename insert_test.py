import os
import uuid # 追加
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime, timezone

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL または SUPABASE_KEY が .env ファイルに設定されていません。")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def insert_sample_post():
    # 実行するたびにユニークなIDを自動生成する (例: test_post_a1b2c3...)
    unique_id = f"test_post_{uuid.uuid4().hex[:8]}"

    data_to_insert = {
        "platform": "X",
        "post_text": f"神戸市はなぜ再開発するのか？ おかしいだろう！ #{uuid.uuid4().hex[:4]}",
        "likes_count": 25,
        "original_post_id": unique_id, # 毎回違うIDになる
        "collected_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        # 毎回新しいデータとして追加される
        response = supabase.table("sns_posts").insert(data_to_insert).execute()
        print("データの追加に成功しました:", response.data)
    except Exception as e:
        print("データの送信に失敗しました:", e)

if __name__ == "__main__":
    insert_sample_post()