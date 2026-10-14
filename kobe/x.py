import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from ai import get_ai_engine  # ai.py を直接呼び出す
from database import get_supabase_client  # データベース接続用
from dotenv import load_dotenv

load_dotenv()  

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.x.com/2/tweets/search/recent"

class XPostClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
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
        
    def save_posts_to_db(self, posts: List[Dict]) -> List[Dict]:
        """
        取得した投稿を事前に sns_posts テーブルへ保存し、DB側で採番された主キーIDを安全に取得する
        """
        saved_posts = []
        for post in posts:
            try:
                post_id = None
                
                # 1. 既に同じ original_post_id が存在するかチェック
                existing = self.supabase.table("sns_posts").select("id").eq("original_post_id", post["original_post_id"]).execute()
                
                if existing and getattr(existing, "data", None):
                    data_list = existing.data
                    if isinstance(data_list, list) and len(data_list) > 0:
                        first_item = data_list[0]
                        # 辞書型かオブジェクト型かに応じて安全にIDを取り出す
                        if isinstance(first_item, dict):
                            post_id = first_item.get("id")
                        else:
                            post_id = getattr(first_item, "id", None)
                
                # 2. 存在しない場合は新規データを挿入
                if not post_id:
                    res = self.supabase.table("sns_posts").insert(post).execute()
                    if res and getattr(res, "data", None):
                        res_list = res.data
                        if isinstance(res_list, list) and len(res_list) > 0:
                            first_item = res_list[0]
                            if isinstance(first_item, dict):
                                post_id = first_item.get("id")
                            else:
                                post_id = getattr(first_item, "id", None)
                
                # それでも取得できなかった場合はスキップ
                if not post_id:
                    print(f"Skipping post {post['original_post_id']} due to missing DB ID.")
                    continue
                
                # AI分析へ渡すデータにDB上の主キー（db_id）を付与
                post_copy = post.copy()
                post_copy["db_id"] = post_id
                saved_posts.append(post_copy)
                
            except Exception as e:
                print(f"sns_posts Insert Error for {post.get('original_post_id')}: {e}")
                
        return saved_posts
    
    def send_to_ai(self, posts: List[Dict]):
        """
        sns_posts テーブルへ保存完了後、そのIDを紐づけてAi.pyへ分析を実行させる
        """
        saved_posts = self.save_posts_to_db(posts)
        
        for post in saved_posts:
            try:
                print(f"-> AIへデータを送信中 (DB ID: {post['db_id']}, Original ID: {post['original_post_id']})")
                
                self.ai_engine.generate_analysis(
                    post_id=post["db_id"],  # データベース上の主キーを渡すことで外部キーエラーを防止
                    text=post["post_text"],
                    like_count=post["likes_count"],
                    reply_count=post["replies_count"]
                )
            except Exception as e:
                print(f"AI Processing Error: {e}")
                
    def run_collection_task(self, keywords: List[str]):
        """
        Xからデータを取得し、sns_postsへの保存を経てAIへ流し込むタスクを実行する
        """
        print("X (Twitter) データ収集およびAI連携タスクを開始します...")
        for keyword in keywords:
            posts = self.fetch_posts_by_keyword(keyword)
            if posts:
                self.send_to_ai(posts)
            
            time.sleep(2)
        print("すべての処理が完了しました。")

if __name__ == "__main__":
    client = XPostClient()
    target_keywords = ["神戸市 不満", "神戸市 改善", "三宮駅 混雑", "垂水区"]
    client.run_collection_task(target_keywords)