import os
import time
from dotenv import load_dotenv
from supabase import create_client, Client

# .envファイルから環境変数を読み込む
load_dotenv()

# 正しくURLとKEYを紐づける
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Pylanceの型エラー（str | None）を防ぐためのアサーション
assert SUPABASE_URL is not None, "SUPABASE_URL が .env に設定されていません"
assert SUPABASE_KEY is not None, "SUPABASE_KEY が .env に設定されていません"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def insert_sns_post(post_data: dict):
    """
    sen_posts テーブルにデータを挿入する関数
    """
    try:
        response = supabase.table("sns_posts").insert(post_data).execute()
        print("挿入成功:", response)
        return response
    except Exception as e:
        print("挿入エラー:", e)
        raise e

# 送信するデータ（original_post_id が重複しないように現在の時間を付与）
new_post = {
    "platform": "X",#プラットフォーム　スレッズ　X Yahoo!ニュース
    "original_post_id": f"test_post_{int(time.time())}",  # 毎回ユニークなIDになるようにする
    "post_text": "",
    "replies_count": 0#返信の数
}

# コメントアウトを外して実行
if __name__ == "__main__":
    insert_sns_post(new_post)