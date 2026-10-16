import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from ai import * # ai.py を直接呼び出す
from database import get_supabase_client  # データベース接続用
from dotenv import load_dotenv
import re
load_dotenv()  

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/all"

class XPostClient:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        # Ai.py 側のエンジンを初期化
        self.ai_engine = get_ai_engine()

    def fetch_posts_by_keyword(self, keywords: List[str], operator: str = "AND", max_results: int = 50) -> List[Dict]:
        """
        X APIを用いて指定キーワードの投稿を取得する
        operator: "OR"（いずれかを含む） または "AND"（すべて含む）
        """
        # クリーニング処理
        cleaned_words = [w.replace('"', '').replace("'", "").strip() for w in keywords if w.strip()]
        
        if not cleaned_words:
            return []

        # キーワードを半角スペースで結合（X APIのAND条件仕様）
        keyword_query = " ".join([f'"{w}"' for w in cleaned_words])
        
        # 広告・スパム・PR・求人・アダルト等およびニュースbot系を除外するクエリ
        exclusions = [
            "-is:retweet", 
            "-PR", "-広告", "-宣伝", "-求人",
            "-AV", "-アダルト", "-風俗", "-マッチングアプリ",
            "-has:links",      # ニュース記事やまとめサイトへのリンク付き投稿を除外
            "-is:nullcast",    # 広告配信専用ツイートを除外
            "-ニュース", "-速報", "-新聞" # ニュースbot系キーワードの除外
        ]
        
        query = f'{keyword_query} {" ".join(exclusions)} lang:ja'
        print(f"Starting collection with [{operator}] query: {query}")

        params = {
            "query": query,
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
                print(f"X API Error: {response.status_code}\n{response.text}")
                return []

            data = response.json()
            tweets = data.get("data", [])
            
            print(f"DEBUG: X APIから取得できた生のツイート数: {len(tweets)}件")

            processed_posts = []
            for tweet in tweets:
                text = tweet.get("text", "")
            
                # 1. 地域キーワード（行政区・主要駅・スポットなど）の判定
                kobe_keywords = [
                    "神戸", "神戸市", "東灘区", "灘区", "中央区", "兵庫区", "長田区", "須磨区", "垂水区", "北区", "西区",
                    "三宮", "三ノ宮", "元町", "新長田", "板宿", "名谷", "学園都市", "西神中央", "有馬", "六甲", "ポートアイランド",
                    "神戸駅", "三ノ宮駅", "新神戸", "神戸空港"
                ]
                if not any(kw in text for kw in kobe_keywords):
                    continue

                # 2. 不満・若者言葉・スラング（オワコン表現など）のチェック
                # ※「神戸市」周辺の愚痴や不満、ネットスラングを含んでいるかを判定
                dissatisfaction_keywords = [
                    "不満", "不平", "オワコン", "終わってる", "終わってない", "無理", "しんどい", 
                    "クソ", "ゴミ", "最悪", "勘弁", "改善", "ダメ", "ひどい", "酷い", 
                    "使えない", "不便", "改悪", "センスない", "何やってる", "税金の無駄"
                ]
                
                # 地域名だけでなく、不満・若者言葉のニュアンスも含まれているかチェック（精度を劇的に上げるため）
                has_grievance = any(dk in text for dk in dissatisfaction_keywords)
                
                # 3. テキスト長とスパム対策のフィルタリング（短い文字・長い文字の両方に対応）
                clean_text = re.sub(r'https?://\S+|#\w+', '', text).strip()
                clean_len = len(clean_text)

                # 極端な短文（10文字未満）は除外
                if clean_len < 10:
                    continue

                # もし不満・若者言葉が含まれておらず、かつ文章が短い場合はノイズとして除外
                if clean_len <= 35 and not has_grievance:
                    continue

                # 改行多すぎスパムを除外
                if text.count("\n") > 5:
                    continue

                metrics = tweet.get("public_metrics", {})
                post_data = {
                    "platform": "X",
                    "original_post_id": str(tweet.get("id")),
                    "post_text": text,
                    "likes_count": metrics.get("like_count", 0),
                    "replies_count": metrics.get("reply_count", 0),
                    "posted_at": tweet.get("created_at"),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
                processed_posts.append(post_data)
                print(f"保存対象 ({clean_len}文字): {text}")

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
                
                if not post_id:
                    print(f"Skipping post {post['original_post_id']} due to missing DB ID.")
                    continue
                
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
                    post_id=post["db_id"],
                    text=post["post_text"],
                    like_count=post["likes_count"],
                    reply_count=post["replies_count"]
                )
            except Exception as e:
                print(f"AI Processing Error: {e}")
                
    def run_collection_task(self, keywords: List[str], operator: str = "OR"):
        """
        外から渡されたキーワードのリストを使ってデータ収集を実行する
        """
        print(f"X (Twitter) データ収集およびAI連携タスクを開始します... キーワード: {keywords}")
        
        # 渡されたキーワードリストを使って検索
        posts = self.fetch_posts_by_keyword(keywords, operator=operator)
        if posts:
            self.send_to_ai(posts)
            
        print("すべての処理が完了しました。")
        
if __name__ == "__main__":
    client = XPostClient()
    test_keywords = ["神戸市","オワコン"]
    client.run_collection_task(keywords=test_keywords, operator="OR")
    print("=== AND検索を実行==")
    client.run_collection_task(keywords=test_keywords,operator="AND")