import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from ai import get_ai_engine  # ai.py を直接呼び出す

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"

class XApiClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        # Ai.py 側のエンジンを初期化
        self.ai_engine = get_ai_engine()

    def fetch_posts_by_keyword(self, keyword: str, max_results: int = 10) -> List[Dict]:
        """
        X APIを用いて指定キーワードの投稿を取得する（フォロワー1万人以上のアカウントを除外）
        """
        params = {
            "query": f"{keyword} -is:retweet lang:ja",
            "max_results": max_results,
            "tweet.fields": "created_at,public_metrics,author_id",
            "user.fields": "public_metrics",
            "expansions": "author_id"
        }
        
        try:
            response = requests.get(SEARCH_ENDPOINT, headers=self.headers, params=params)
            
            if response.status_code == 429:
                print("API Error: X APIのレート制限（Rate Limit）に達しました。")
                return []
            elif response.status_code != 200:
                print(f"X API Error: {response.status_code} - {response.text}")
                return []

            data = response.json()
            tweets = data.get("data", [])

            includes = data.get("includes", {})
            users = includes.get("users", [])
            user_followers_map = {}
            for user in users:
                user_id = user.get("id")
                metrics = user.get("public_metrics", {})
                user_followers_map[user_id] = metrics.get("followers_count", 0)

            processed_posts = []
            for tweet in tweets:
                author_id = tweet.get("author_id")
                followers_count = user_followers_map.get(author_id, 0)
                
                if followers_count >= 10000:
                    continue

                metrics = tweet.get("public_metrics", {})
                post_data = {
                    "platform": "X",
                    "original_post_id": str(tweet.get("id")),
                    "post_text": tweet.get("text"),
                    "likes_count": metrics.get("like_count", 0),
                    "replies_count": metrics.get("reply_count", 0),
                    "posted_at": tweet.get("created_at"),
                    "collected_at": datetime.now(timezone.utc).isoformat()
                }
                processed_posts.append(post_data)

            return processed_posts

        except Exception as e:
            print(f"Network/Connection Error in X.py: {e}")
            return []
        
    def send_to_ai(self, posts: List[Dict]):
        """
        取得した投稿データをそのまま Ai.py へ引き渡して分析を実行する
        """
        for post in posts:
            try:
                print(f"-> Ai.pyへデータを送信中 (Original ID: {post['original_post_id']})")
                
                # ai.py の generate_analysis には text と like_count だけを渡す
                self.ai_engine.generate_analysis(
                    text=post["post_text"],
                    like_count=post["likes_count"]
                )
            except Exception as e:
                print(f"AI Processing Error: {e}")
                
    def run_collection_task(self, keywords: List[str]):
        """
        Xからデータを取得し、直接Ai.pyへ流し込むタスクを実行する
        """
        print("X (Twitter) データ収集およびAI連携タスクを開始します...")
        for keyword in keywords:
            posts = self.fetch_posts_by_keyword(keyword)
            if posts:
                self.send_to_ai(posts)
            
            time.sleep(2)
        print("すべての処理が完了しました。")

if __name__ == "__main__":
    client = XApiClient()
    target_keywords = ["神戸市 不満", "神戸市 改善", "三宮駅 混雑", "垂水区"]
    client.run_collection_task(target_keywords)