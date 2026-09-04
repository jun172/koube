import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from database import get_supabase_client

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"

class XApiClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()

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

            # APIレスポンスの includes からユーザー情報を取得し、IDごとのフォロワー数をマッピング
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
                
                # フォロワー数が1万人以上のアカウントの投稿はスキップ
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

    def save_posts_to_db(self, posts: List[Dict]):
        """
        取得した投稿を Supabase (sns_posts) に保存する
        original_post_id の UNIQUE 制約により重複収集を防止
        """
        for post in posts:
            try:
                self.supabase.table("sns_posts").upsert(
                    post, 
                    on_conflict="original_post_id"
                ).execute()
                print(f"Saved/Updated post ID: {post['original_post_id']}")
            except Exception as e:
                print(f"DB Insert Error: {e}")

    def run_collection_task(self, keywords: List[str]):
        """
        X（Twitter）データ収集タスクを実行する
        """
        print("X (Twitter) データ収集タスクを開始します...")
        for keyword in keywords:
            posts = self.fetch_posts_by_keyword(keyword)
            if posts:
                self.save_posts_to_db(posts)
            
            time.sleep(2)

if __name__ == "__main__":
    client = XApiClient()
    target_keywords = ["神戸市 不満", "神戸市 改善", "三宮駅 混雑","垂水区",]
    client.run_collection_task(target_keywords)