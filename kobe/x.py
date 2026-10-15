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
        X APIを用いて指定キーワードの投稿を取得する（ノイズ除外、フォロワー制限、地域・文脈フィルターを適用）
        """
        # キーワードをダブルクォーテーションで囲み、PRやリツイートを除外するクエリ
        query = f'"{keyword}" -is:retweet -PR -キャンペーン -求人 lang:ja'
        
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
                
                # フォロワー1万人以上のアカウントを除外
                if followers_count >= 10000:
                    continue

                text = tweet.get("text", "")
                
                # 1. 神戸市の全行政区＋主要なエリア・駅名を含めたリスト（地域フィルター）
                kobe_keywords = [
                    # 市名・広域
                    "神戸", "神戸市",
                    # 神戸市の9区（「〇〇区」と区名単体の両方に対応）
                    "東灘区", "灘区", "中央区", "兵庫区", "長田区", "須磨区", "垂水区", "北区", "西区",
                    "東灘", "灘", "兵庫", "長田", "須磨", "垂水",
                    # 主要な駅・エリア・通称
                    "三宮", "三ノ宮", "元町", "新長田", "板宿", "名谷", "学園都市", 
                    "西神中央", "有馬", "六甲", "岡本", "御影", "ポートアイランド", "六甲アイランド"
                ]
                
                # いずれの地域ワードも本文に含まれていない場合はスキップ
                if not any(kw in text for kw in kobe_keywords):
                    continue

                # 2. 【配列の外】「不満」やインフラ・子育てなど行政課題・市民の声に関するキーワードチェック
                # 2. 【配列の外】「不満」やインフラ・子育てなど行政課題・市民の声に関するキーワードチェック
                complaint_keywords = [
                    # 感情・評価
                    "不満", "改善", "要望", "困", "問題", "どうにか", "危険", "ひどい", "なんとか", 
                    "不便", "遅い", "足りない", "汚い", "怖い", "最悪", "邪魔", "うるさい", "嫌", "対応",
                    # 交通・道路・街
                    "道路", "インフラ", "渋滞", "混雑", "遅延", "バス", "電車", "駅", "歩道", "街灯", 
                    "工事", "通行止め", "駐輪", "路駐", "事故", "信号",
                    # 子育て・暮らし・行政
                    "子育て", "保育", "学校", "給食", "児童", "医療", "公園", "ゴミ", "騒音", "市役所", "行政"
                ]
                
                # 地域名が入っていても、上記の文脈ワードが一切なければスキップ
                if not any(cw in text for cw in complaint_keywords):
                    continue
                
                # 重複を排除して、マッチしたキーワードの種類数をカウント
                matched_complaints = set(cw for cw in complaint_keywords if cw in text)

                # 不満・課題関連のキーワードが【2種類以上】入っている場合のみ保存
                if len(matched_complaints) >= 3:
                    # データの構築
                    metrics = tweet.get("public_metrics", {})
                    post_data = {
                        "platform": "X",
                        "original_post_id": str(tweet.get("id")),
                        "post_text": text,
                        "likes_count": metrics.get("like_count", 0),
                        "replies_count": metrics.get("reply_count", 0),
                    }
                    
                    # TODO: データベースへの保存処理（例: db.insert_post(post_data) など）をここに記述
                    # 例としてプリント出力またはリストへの追加に置き換えてもOKです
                    print(f"保存対象: {text}")
                
                metrics = tweet.get("public_metrics", {})
                post_data = {
                    "platform": "X",
                    "original_post_id": str(tweet.get("id")),
                    "post_text": text,
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