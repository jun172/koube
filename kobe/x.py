import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict,Optional
from database import get_supabase_client  # データベース接続用
from dotenv import load_dotenv
import pandas as pd
import re
import random

load_dotenv()  

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"

class XPostClient:
    def __init__(self, master_csv: str = "kobe_keywords_master.csv"):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        
        # 1. 945件のマスターリスト（町名・駅名）をローカル判定用に読み込む
        self.kobe_keywords = self._load_kobe_master(master_csv)
        
        # 2. API収集フェーズ（広め）で使う代表的な大エリアの配列
        self.api_broad_keywords = [
            "神戸", "神戸市", "三宮", "三ノ宮", "元町", 
            "中央区", "東灘区", "灘区", "兵庫区", "長田区", 
            "須磨区", "垂水区", "北区", "西区"
        ]
        
        # 3. 不満・トラブル・インフラの困りごとに特化したキーワードリスト
        self.dissatisfaction_keywords = [
            # 一般的な不満・批判表現
            "不満", "不平", "オワコン", "終わってる", "無理", "しんどい", 
            "クソ", "最悪", "勘弁", "改善して", "改善求める", "ダメだ", "ひどい", "酷い", 
            "使えない", "不便", "改悪", "センスない", "何やってる", "税金の無駄", "困る", "直して",
            
            # 道路・インフラ・環境のトラブル
            "道路の穴", "道路陥没", "陥没", "舗装が", "街灯が切れて", "街灯暗い", "街灯壊れ",
            "ゴミ放置", "ゴミ屋敷", "不法投棄", "ポイ捨て", "悪臭", "異臭", "汚い", 
            "放置自転車", "放置バイク", "雑草", "害虫", "騒音", "うるさい",
            "子育て", "トラブル", "危険", "事故"
        ]

    def _load_kobe_master(self, master_csv: str) -> set:
        """
        kobe_keywords_master.csv から地域・駅名キーワードを読み込み、不要なワードを除外する
        """
        areas = set()
        try:
            df_master = pd.read_csv(master_csv)
            col_name = 'keyword' if 'keyword' in df_master.columns else df_master.columns[0]
            
            for _, row in df_master.iterrows():
                val = str(row.get(col_name, '')).strip()
                # 無効なワードやSNSで使われないプレースホルダーを除外
                if val and val != "nan" and "以下に掲載がない場合" not in val and "その他" not in val:
                    areas.add(val)
            print(f"📌 {master_csv} から有効なキーワード {len(areas)} 件をロードしました。")
        except FileNotFoundError:
            print(f"⚠️ 警告: {master_csv} が見つかりません。デフォルトの主要ワードを使用します。")
            areas.update(["神戸", "神戸市", "中央区", "三宮", "元町", "三ノ宮"])

        return areas

    def fetch_posts_by_keyword(self, keywords: Optional[List[str]] = None, operator: str = "OR", max_results: int = 10) -> List[Dict]:
        if not self.kobe_keywords:
            return []

        # 引数でキーワードが渡された場合はそれを使用し、無ければマスターからランダム選択
        if keywords:
            keyword_query = f" {operator} ".join([f'"{w}"' for w in keywords])
        else:
            # プレースホルダーとして直接ランダム選択する
            random_targets = random.sample(list(self.kobe_keywords), min(5, len(self.kobe_keywords)))
            keyword_query = f" {operator} ".join([f'"{w}"' for w in random_targets])

        # 不満・ネガティブキーワード
        dissatisfaction_query = " OR ".join([f'"{d}"' for d in ["不満", "最悪", "ひどい", "不便", "改善", "汚い", "危険", "うるさい", "困る", "直して"]])
        
        exclusions = [
            "-is:retweet", "-PR", "-広告", "-宣伝", "-求人",
            "-AV", "-アダルト", "-風俗", "-マッチングアプリ",
            "-has:links", "-is:nullcast", "-ニュース", "-速報", "-新聞"
        ]
        
        query = f'({keyword_query}) ({dissatisfaction_query}) {" ".join(exclusions)} lang:ja'
        print(f"Starting optimized X collection query: {query}")
        
        # 以下、既存のparamsやrequests処理へ続く...
        print(f"Query length: {len(query)} chars")

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
                print("API Error: X APIのレート制限に達しました。")
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
            
                # ---------------------------------------------------------
                # ローカル判定フェーズ（柔軟なORチェック）
                # ---------------------------------------------------------
                
                # 1. マスターリスト（945件の町名・駅名・区名）のいずれかが本文に含まれているかチェック
                matched_area = next((kw for kw in self.kobe_keywords if kw in text), None)
                if not matched_area:
                    continue

                # 2. 不満・ネガティブキーワード判定
                matched_grievance = next((dk for dk in self.dissatisfaction_keywords if dk in text), None)
                has_grievance = matched_grievance is not None
                
                # 3. テキスト長や改行数のチェック（スパム等の除外）
                clean_text = re.sub(r'https?://\S+|#\w+', '', text).strip()
                if len(clean_text) < 10 or not has_grievance or text.count("\n") > 5:
                    continue

                metrics = tweet.get("public_metrics", {})
                post_data = {
                    "platform": "X",
                    "original_post_id": str(tweet.get("id")),
                    "post_text": text,
                    "matched_area": matched_area,  # ← どの町・駅・区にヒットしたかを保存
                    "likes_count": metrics.get("like_count", 0),
                    "replies_count": metrics.get("reply_count", 0),
                    "posted_at": tweet.get("created_at"),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
                processed_posts.append(post_data)
                print(f"🎯 保存対象 [一致エリア: {matched_area}, 不満ワード: {matched_grievance}]: {text}")

            return processed_posts

        except Exception as e:
            print(f"Network/Connection Error in X.py: {e}")
            return []

    def save_posts_to_db(self, posts: List[Dict]):
        """フィルタリングを通過した投稿を Supabase へ保存する"""
        saved_count = 0
        for post in posts:
            try:
                existing = self.supabase.table("sns_posts").select("id").eq("original_post_id", post["original_post_id"]).execute()
                
                exists = False
                if existing and getattr(existing, "data", None):
                    data_list = existing.data
                    if isinstance(data_list, list) and len(data_list) > 0:
                        exists = True
                
                if not exists:
                    self.supabase.table("sns_posts").insert(post).execute()
                    saved_count += 1
                    print(f"💾 DB保存成功: {post['original_post_id']}")
                else:
                    print(f"⏩ 既存のためスキップ: {post['original_post_id']}")
                    
            except Exception as e:
                print(f"❌ sns_posts Insert Error for {post.get('original_post_id')}: {e}")
                
        print(f"📊 データベース保存処理完了: 新規 {saved_count} 件を保存しました。")

    def run_collection_task(self):
        print("Kobe Master Keywords 2段階フィルタリングによるXデータ収集タスクを開始します...")
        posts = self.fetch_posts_by_keyword(max_results=10)
        if posts:
            self.save_posts_to_db(posts)
        else:
            print("条件に一致する新規投稿はありませんでした。")
        print("すべての処理が完了しました。")
        
if __name__ == "__main__":
    client = XPostClient()
    client.run_collection_task()