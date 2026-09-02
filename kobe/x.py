import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from database import get_supabase_client # データベース接続クライアント

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"

class XApiClient:
    def __init__(self):
        # GETリクエストのためContent-Typeは不要、あるいは正しい形式に設定
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()

    def fetch_posts_by_keyword(self, keyword: str, max_results: int = 10) -> List[Dict]:
        """
        X APIを用いて指定キーワードの投稿を取得する
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
            
            # API制限（Rate Limit）やエラーのハンドリング
            if response.status_code == 429:
                print("API Error: X APIのレート制限（Rate Limit）に達しました。")
                return []
            elif response.status_code != 200:
                print(f"X API Error: {response.status_code} - {response.text}")
                return []

            data = response.json()
            tweets = data.get("data", [])
            users = {user["id"]: user for user in data.get("includes", {}).get("users", [])}

            processed_posts = []
            for tweet in tweets:
                author_id = tweet.get("author_id")
                user_info = users.get(author_id, {})
                user_metrics = user_info.get("public_metrics", {})
                
                # フォロワー数を取得（デフォルト0）
                follower_count = user_metrics.get("followers_count", 0)

                # 設計書に基づく要件: 5000人以上のアカウント（インフルエンサー等）の除外
                if follower_count >= 5000:
                    print(f"Skipped (Influencer): フォロワー数 {follower_count} 人のため除外します。")
                    continue

                metrics = tweet.get("public_metrics", {})
                post_data = {
                    "platform": "X",
                    "original_post_id": str(tweet.get("id")),
                    "post_text": tweet.get("text"),
                    "likes_count": metrics.get("like_count", 0),       # テーブル定義(likes_count)に合わせる
                    "replies_count": metrics.get("reply_count", 0),   # テーブル定義(replies_count)に合わせる
                    "posted_at": tweet.get("created_at"),
                    "collected_at": datetime.now(timezone.utc).isoformat()
                }
                processed_posts.append(post_data)

            return processed_posts

        except Exception as e:
            print(f"Network/Connection Error in X.py: {e}")
            return []

    def save_posts_to_db(self, posts: List[Dict]):
        """
        取得した投稿を Supabase (sns_posts) に保存する
        original_post_id の UNIQUE 制約により重複収集をシステムレベルで防止する
        """
        for post in posts:
            try:
                response = self.supabase.table("sns_posts").upsert(
                    post, 
                    on_conflict="original_post_id"
                ).execute()
                print(f"Saved/Updated post ID: {post['original_post_id']}")
            except Exception as e:
                print(f"DB Insert Error: {e}")

    def run_collection_task(self, keywords: List[str]):
        """
        定期実行用タスク: 
        設計書のインターバルを考慮して安全に収集を行う
        """
        print("X (Twitter) データ収集タスクを開始します...")
        for keyword in keywords:
            posts = self.fetch_posts_by_keyword(keyword)
            if posts:
                self.save_posts_to_db(posts)
            
            # API制限対策としてのインターバル
            time.sleep(2)

if __name__ == "__main__":
    client = XApiClient()
    target_keywords = ["神戸市 不満", "神戸市 改善", "三宮駅 混雑"]
    client.run_collection_task(target_keywords)