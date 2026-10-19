import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Tuple,Optional
from database import get_supabase_client
from dotenv import load_dotenv
import pandas as pd
import re

load_dotenv()  

X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"

class XPostCollector:
    def __init__(self, kobe_csv: str = "kobe.csv", stations_csv: str = "kobe_stations.csv", sentiment_csv: str = "kobe_sentiment_keywords.csv"):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        
        # 1. 各CSVからリストをしっかりとロード
        self.towns = self._load_csv_column(kobe_csv, 'town')
        self.stations = self._load_csv_column(stations_csv, 'station_name') # ★ 駅名の列名 'station_name' を指定
        self.positive_keywords, self.negative_keywords = self._load_sentiments(sentiment_csv)
        
        self.state_dir = "collection_states"
        os.makedirs(self.state_dir, exist_ok=True)

        print(f"📌 ロード完了 -> 町名: {len(self.towns)}件 / 駅名: {len(self.stations)}件 / 感情ワード(ポジ/ネガ): {len(self.positive_keywords)}/{len(self.negative_keywords)}件")

    def _load_csv_column(self, path: str, col_name: str) -> List[str]:
        items = []
        try:
            df = pd.read_csv(path)
            if col_name in df.columns:
                for _, row in df.iterrows():
                    val = str(row.get(col_name, '')).strip()
                    if val and val != "nan" and "以下に掲載がない場合" not in val:
                        if val not in items:
                            items.append(val)
        except FileNotFoundError:
            print(f"⚠️ 警告: {path} が見つかりません。")
        return items

    def _load_sentiments(self, path: str) -> Tuple[List[str], List[str]]:
        pos, neg = [], []
        try:
            df = pd.read_csv(path)
            if 'positive_keyword' in df.columns:
                pos = [str(x).strip() for x in df['positive_keyword'].dropna() if str(x).strip() != "nan"]
            if 'negative_keyword' in df.columns:
                neg = [str(x).strip() for x in df['negative_keyword'].dropna() if str(x).strip() != "nan"]
        except FileNotFoundError:
            pass
        return pos, neg

    def _get_one_item_with_rollover(self, items: List[str], state_name: str) -> Tuple[str, bool]:
        """リストから1件ずつ順に取り出し、末尾に達したらTrueを返す"""
        if not items:
            return "", True
            
        state_path = os.path.join(self.state_dir, f"{state_name}_index.txt")
        current_idx = 0
        
        if os.path.exists(state_path):
            try:
                with open(state_path, "r") as f:
                    current_idx = int(f.read().strip())
            except ValueError:
                current_idx = 0
        
        total_len = len(items)
        if current_idx >= total_len:
            return items[-1], True  # 終端に達している場合は最後の要素を返す
            
        item = items[current_idx]
        next_idx = current_idx + 1
        is_finished = (next_idx >= total_len)
        
        with open(state_path, "w") as f:
            f.write(str(next_idx))
            
        return item, is_finished

    def fetch_posts_by_keyword(self, max_results: int = 10) -> bool:
        """
        町名、駅名、感情キーワードをそれぞれ1件ずつ取り出してクエリを組み立てる。
        """
        town, towns_finished = self._get_one_item_with_rollover(self.towns, "towns")
        station, stations_finished = self._get_one_item_with_rollover(self.stations, "stations")
        
        all_sentiments = self.positive_keywords + self.negative_keywords
        sentiment, sentiments_finished = self._get_one_item_with_rollover(all_sentiments, "sentiments")

        # 検索クエリの組み立て ("神戸" OR "神戸市" OR 町名 OR 駅名) AND (感情キーワード)
        area_query = f'"神戸" OR "神戸市" OR "{town}" OR "{station}"'
        sentiment_query = f'"{sentiment}"' if sentiment else '"最高" OR "不満"'

        exclusions = [
            "-is:retweet", "-PR", "-広告", "-宣伝", "-求人",
            "-AV", "-アダルト", "-風俗", "-マッチングアプリ",
            "-has:links", "-is:nullcast", "-ニュース", "-速報", "-新聞"
        ]
        
        query = f'({area_query}) ({sentiment_query}) {" ".join(exclusions)} lang:ja'
        print(f"\n🔍 検索ターゲット [町名: {town} / 駅名: {station} / 感情: {sentiment}]")
        print(f"🚀 X検索実行クエリ: {query}")

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
                print("⚠️ API Error: X APIのレート制限に達しました。15分待機します...")
                time.sleep(900)
                return False
            elif response.status_code != 200:
                print(f"❌ X API Error: {response.status_code}\n{response.text}")
                return towns_finished and stations_finished

            data = response.json()
            tweets = data.get("data", [])
            print(f"📥 取得した生のツイート数: {len(tweets)}件")

            saved_count = 0
            for tweet in tweets:
                text = tweet.get("text", "")
                clean_text = re.sub(r'https?://\S+|#\w+', '', text).strip()
                if len(clean_text) < 8 or text.count("\n") > 5:
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
                
                try:
                    existing = self.supabase.table("sns_posts").select("id").eq("original_post_id", post_data["original_post_id"]).execute()
                    if not existing.data:
                        self.supabase.table("sns_posts").insert(post_data).execute()
                        saved_count += 1
                except Exception as db_e:
                    print(f"❌ DB Error: {db_e}")

            print(f"📊 この検索での新規保存: {saved_count} 件")
            return towns_finished and stations_finished

        except Exception as e:
            print(f"💥 通信エラー: {e}")
            return False

    def run_full_scan(self):
        print("=== 町名・駅名・感情ワードの全件フルスキャン（whileループ）を開始 ===")
        
        loop_count = 0
        max_safety_loops = max(len(self.towns), len(self.stations)) + 50

        while loop_count < max_safety_loops:
            loop_count += 1
            print(f"\n--- スキャンステップ: {loop_count} / 最大 {max_safety_loops} ---")
            
            is_finished = self.fetch_posts_by_keyword(max_results=10)
            
            if is_finished:
                print("🎉 すべてのマスターデータの最後までスキャンし切りました！")
                break
                
            time.sleep(2)

        print("=== すべての全自動スキャンが完了しました ===")

if __name__ == "__main__":
    collector = XPostCollector()
    collector.run_full_scan()