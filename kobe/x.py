import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict
from database import get_supabase_client  # データベース接続用
from dotenv import load_dotenv
import pandas as pd
import re
load_dotenv()  

# X API設定
X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/"

class XPostClient:
    def __init__(self, kobe_csv: str = "kobe.csv", station_csv: str = "kobe_stations.csv"):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        
        # kobe.csv と kobe_stations.csv からエリア・町名・駅名を自動ロードしてセットを構築
        self.kobe_keywords = self._load_kobe_areas(kobe_csv, station_csv)
        
        # 不満・若者言葉・スラング・インフラ不満キーワードのチェックリスト
        # 不満・トラブル・インフラの困りごとに特化したキーワードリスト（誤検知防止版）
        self.dissatisfaction_keywords = [
            # 一般的な不満・批判表現
            "不満", "不平", "オワコン", "終わってる", "無理", "しんどい", 
            "クソ", "最悪", "勘弁", "改善して", "改善求める", "ダメだ", "ひどい", "酷い", 
            "使えない", "不便", "改悪", "センスない", "何やってる", "税金の無駄",
            
            # 道路・インフラ・環境のトラブル
            "道路の穴", "道路陥没", "陥没", "舗装が", "街灯が切れて", "街灯暗い", "街灯壊れ",
            "ゴミ放置", "ゴミ屋敷", "不法投棄", "ポイ捨て", "悪臭", "異臭", "汚い", 
            "放置自転車", "放置バイク", "雑草", "害虫", "騒音", "うるさい",
            "子育て"
        ]

    def _load_kobe_areas(self, kobe_csv: str, station_csv: str) -> set:
        """
        kobe.csv（区・町名）と kobe_stations.csv（駅名）を読み込み、重複のない総合エリアキーワードセットを構築する
        """
        areas = set()
        
        # 1. kobe.csv の読み込み
        try:
            df_towns = pd.read_csv(kobe_csv)
            for _, row in df_towns.iterrows():
                city = str(row.get('city', '')).strip()
                town = str(row.get('town', '')).strip()
                
                if city:
                    areas.add(city)
                    areas.add(city.replace("神戸市", "")) # 短縮形
                if town and "以下に掲載がない場合" not in town:
                    areas.add(town)
            print(f"📌 {kobe_csv} から町名・区データをロードしました。")
        except FileNotFoundError:
            print(f"⚠️ 警告: {kobe_csv} が見つかりません。デフォルトの主要ワードを使用します。")
            areas.update(["神戸", "神戸市", "中央区", "三宮", "元町"])

        # 2. kobe_stations.csv の読み込み（station_name 列のみ）
        try:
            df_stations = pd.read_csv(station_csv)
            for _, row in df_stations.iterrows():
                station_name = str(row.get('station_name', '')).strip()
                if station_name:
                    areas.add(station_name)
                    areas.add(f"{station_name}駅")
            print(f"📌 {station_csv} から駅名データをロードしました。")
        except FileNotFoundError:
            print(f"⚠️ 警告: {station_csv} が見つかりません。")

        # 通称・エイリアスの補強
        areas.update(["ポートアイランド", "六甲アイランド", "港島", "三ノ宮", "ハーバーランド"])
        
        print(f"✅ 総合エリアキーワード総数: {len(areas)} 件")
        return areas

    def fetch_posts_by_keyword(self, keywords: List[str], operator: str = "OR", max_results: int = 10) -> List[Dict]:
        """
        X APIを用いて指定キーワードの投稿を取得する（取得件数を10件に設定）
        """
        cleaned_words = [w.replace('"', '').replace("'", "").strip() for w in keywords if w.strip()]
        
        if not cleaned_words:
            return []

        if operator.upper() == "OR":
            keyword_query = " OR ".join([f'"{w}"' for w in cleaned_words])
        else:
            keyword_query = " ".join([f'"{w}"' for w in cleaned_words])
        
        exclusions = [
            "-is:retweet", 
            "-PR", "-広告", "-宣伝", "-求人",
            "-AV", "-アダルト", "-風俗", "-マッチングアプリ",
            "-has:links",
            "-is:nullcast",
            "-ニュース", "-速報", "-新聞"
        ]
        
        query = f'({keyword_query}) {" ".join(exclusions)} lang:ja'
        print(f"Starting collection with [{operator}] query: {query}")

        params = {
            "query": query,
            "max_results": max_results, # 10件に制限
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
            
                # 1. ローカル辞書（kobe.csv & kobe_stations.csv）のエリア・町名・駅名キーワード判定
                matched_area = next((kw for kw in self.kobe_keywords if kw in text), None)
                if not matched_area:
                    continue

                # 2. 不満・ネガティブキーワード判定
                matched_grievance = next((dk for dk in self.dissatisfaction_keywords if dk in text), None)
                has_grievance = matched_grievance is not None
                
                # 3. テキスト長とスパム対策フィルタリング
                clean_text = re.sub(r'https?://\S+|#\w+', '', text).strip()
                clean_len = len(clean_text)

                if clean_len < 10:
                    continue

                # ★文字数に関わらず、不満キーワードが含まれていない投稿をすべて除外する場合
                if not has_grievance:
                    continue

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
                print(f"保存対象 [一致エリア: {matched_area}, 不満ワード: {matched_grievance}] ({clean_len}文字): {text}")

            return processed_posts

        except Exception as e:
            print(f"Network/Connection Error in X.py: {e}")
            return []

    def save_posts_to_db(self, posts: List[Dict]):
        """
        フィルタリングを通過した投稿を Supabase の sns_posts テーブルへ保存する（AI分析なし）
        """
        saved_count = 0
        for post in posts:
            try:
                # 既に同じ original_post_id が存在するかチェック
                existing = self.supabase.table("sns_posts").select("id").eq("original_post_id", post["original_post_id"]).execute()
                
                exists = False
                if existing and getattr(existing, "data", None):
                    data_list = existing.data
                    if isinstance(data_list, list) and len(data_list) > 0:
                        exists = True
                
                # 存在しない場合のみ新規挿入
                if not exists:
                    self.supabase.table("sns_posts").insert(post).execute()
                    saved_count += 1
                    print(f"💾 DB保存成功: {post['original_post_id']}")
                else:
                    print(f"⏩ 既存のためスキップ: {post['original_post_id']}")
                    
            except Exception as e:
                print(f"❌ sns_posts Insert Error for {post.get('original_post_id')}: {e}")
                
        print(f"📊 データベース保存処理完了: 新規 {saved_count} 件を保存しました。")

    def run_collection_task(self, keywords: List[str], operator: str = "OR"):
        print(f"X (Twitter) データ収集タスクを開始します... キーワード: {keywords} (演算子: {operator})")
        
        # 10件に制限して取得
        posts = self.fetch_posts_by_keyword(keywords, operator=operator, max_results=10)
        if posts:
            # データベースへ直接保存（AI分析はバイパス）
            self.save_posts_to_db(posts)
        else:
            print("条件に一致する新規投稿はありませんでした。")
            
        print("すべての処理が完了しました。")
        
if __name__ == "__main__":
    client = XPostClient()
    test_keywords = ["神戸　不満"]
    client.run_collection_task(keywords=test_keywords, operator="OR")