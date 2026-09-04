import os
import requests
from database import get_supabase_client, insert_threads_comment  # 👈 追加・変更

THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN")
THREADS_USER_ID = os.environ.get("THREADS_USER_ID")
BASE_URL = "https://graph.threads.net/v1.0"

class ThreadsCollector:
    def __init__(self):
        self.access_token = THREADS_ACCESS_TOKEN
        self.user_id = THREADS_USER_ID
        self.supabase = get_supabase_client()

    def fetch_and_save_replies(self):
        posts_url = f"{BASE_URL}/{self.user_id}/threads"
        params = {
            "access_token": self.access_token,
            "fields": "id,text,timestamp"
        }
        
        response = requests.get(posts_url, params=params)
        if response.status_code != 200:
            print(f"Threads API Error (Posts): {response.status_code} - {response.text}")
            return

        threads = response.json().get("data", [])

        for thread in threads:
            thread_id = thread.get("id")
            replies_url = f"{BASE_URL}/{thread_id}/replies"
            reply_params = {
                "access_token": self.access_token,
                "fields": "id,text,username,timestamp"
            }
            
            reply_response = requests.get(replies_url, params=reply_params)
            if reply_response.status_code == 200:
                replies = reply_response.json().get("data", [])
                
                for reply in replies:
                    comment_data = {
                        "platform": "Threads",
                        "post_id": str(thread_id),
                        "comment_id": str(reply.get("id")),
                        "comment_text": reply.get("text"),
                        "author_username": reply.get("username"),
                        "posted_at": reply.get("timestamp")
                    }
                    
                    # database.py 経由でデータベースに保存
                    res = insert_threads_comment(comment_data)
                    if res:
                        print(f"コメント保存成功: {reply.get('text')[:30]}...")
            else:
                print(f"リプライ取得エラー: {reply_response.status_code} - {reply_response.text}")

if __name__ == "__main__":
    collector = ThreadsCollector()
    collector.fetch_and_save_replies()