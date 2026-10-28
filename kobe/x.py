import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
from database import get_supabase_client
from dotenv import load_dotenv
import pandas as pd
import re

load_dotenv()  

X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/all"

class XPostCollector:
    def __init__(
        self, 
        kobe_csv: str = "kobe.csv", 
        stations_csv: str = "kobe_stations.csv", 
        sentiment_csv: str = "kobe_sentiment_keywords.csv",
        exclude_csv: str = "exclude_keywords.csv"
    ):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        
        # 1. 各CSVからリストをロード
        self.cities = self._load_csv_column(kobe_csv, 'city')
        self.towns = self._load_csv_column(kobe_csv, 'town')
        self.stations = self._load_csv_column(stations_csv, 'station_name')
        
        # 除外キーワードCSVのロード
        self.exclude_keywords = self._load_exclude_keywords(exclude_csv)
        
        # ポジティブは除外し、ネガティブ（不満・課題）ワードだけに特化
        _, self.negative_keywords = self._load_sentiments(sentiment_csv)
        
        # 追加の不満・行政要望ワード
        self.complaint_suffixes = ["不満", "改善", "どうにかして", "ひどい", "困る", "危険", "汚い", "遅い", "高い", "対応して", "何とかして"]

        self.state_dir = "collection_states"
        os.makedirs(self.state_dir, exist_ok=True)

        print(f"📌 ロード完了 -> 行政区:{len(self.cities)}件 / 町名: {len(self.towns)}件 / 駅名: {len(self.stations)}件 / ネガティブワード: {len(self.negative_keywords)}件 / 除外ワード: {len(self.exclude_keywords)}件")

    def _load_csv_column(self, path: str, col_name: str) -> List[str]:
        items = []
        try:
            df = pd.read_csv(path)
            if col_name in df.columns:
                for _, row in df.iterrows():
                    val = str(row.get(col_name, '')).strip()
                    if val and val != "nan" and "以下に掲載がない場合" not in val:
                        if val not in ["神戸", "神戸市"] and val not in items:
                            items.append(val)
        except FileNotFoundError:
            print(f"⚠️ 警告: {path} が見つかりません。")
        return items

    def _load_exclude_keywords(self, path: str) -> List[str]:
        """ exclude_keywords.csv から除外ワードを取得 """
        keywords = []
        try:
            df = pd.read_csv(path)
            if 'keyword' in df.columns:
                for val in df['keyword'].dropna():
                    kw = str(val).strip()
                    if kw and kw != "nan":
                        keywords.append(kw)
        except FileNotFoundError:
            print(f"⚠️ 警告: 除外キーワードファイル ({path}) が見つかりません。スキップします。")
        return keywords

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
            return items[-1], True  
            
        item = items[current_idx]
        next_idx = current_idx + 1
        is_finished = (next_idx >= total_len)
        
        with open(state_path, "w") as f:
            f.write(str(next_idx))
            
        return item, is_finished

    def fetch_posts_by_keyword(self, max_results: int = 10) -> bool:
        city, cities_finished = self._get_one_item_with_rollover(self.cities, "cities")
        station, stations_finished = self._get_one_item_with_rollover(self.stations, "stations")
        neg_keyword, neg_finished = self._get_one_item_with_rollover(self.negative_keywords, "negative_sentiments")

        area_targets = []
        if city: area_targets.append(f'"{city}"')
        if station: area_targets.append(f'"{station}"')
        
        area_query = " OR ".join(area_targets) if area_targets else '"神戸市"'
        
        sentiment_list = []
        if neg_keyword: sentiment_list.append(f'"{neg_keyword}"')
        for suffix in ["不満", "改善", "困る", "ひどい", "どうにかして"]:
            sentiment_list.append(f'"{suffix}"')
            
        sentiment_query = " OR ".join(set(sentiment_list))

        exclusions = [
            "-is:retweet", "-PR", "-広告", "-宣伝", "-求人",
            "-AV", "-アダルト", "-風俗", "-株", "-FX", "-投資",
            "-has:links", "-ニュース", "-速報", "-天気予報"
        ]
        
        query = f'(神戸市 OR ({area_query})) ({sentiment_query}) {" ".join(exclusions)} lang:ja'
        
        print(f"\n🔍 検索ターゲット [行政: {city} / 駅: {station} / ネガティブ: {neg_keyword}]")
        print(f"🚀 精度向上クエリ: {query}")

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
                print("⚠️ API Error: レート制限に達しました。15分待機します...")
                time.sleep(900)
                return False
            elif response.status_code != 200:
                print(f"❌ X API Error: {response.status_code}\n{response.text}")
                return stations_finished and cities_finished

            data = response.json()
            tweets = data.get("data", [])
            print(f"📥 取得した生のツイート数: {len(tweets)}件")

            saved_count = 0
            for tweet in tweets:
                text = tweet.get("text", "")
                
                # ----------------------------------------------------
                # 🛑 【事前除外フィルタ】 exclude_keywords.csv のチェック
                # ----------------------------------------------------
                if any(bad_word in text for bad_word in self.exclude_keywords):
                    # スパム・広告・求人・グルメ等のノイズ投稿は保存せずスキップ
                    continue

                # 1. 短すぎるもの、改行が多すぎるものを除外
                clean_text = re.sub(r'https?://\S+|#\w+', '', text).strip()
                if len(clean_text) < 10 or text.count("\n") > 4:
                    continue
                
                # 2. 不満文脈キーワードの再チェック
                complaint_words = ["不満", "改善", "行政", "市役所", "市長", "税金", "バス", "道路", "公園", "ゴミ", "騒音", "危険", "困", "ひど", "対応"]
                has_complaint_context = any(word in text for word in complaint_words)
                
                if not has_complaint_context:
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

            print(f"📊 フィルタリング後の新規保存件数: {saved_count} 件")
            return stations_finished and cities_finished and neg_finished

        except Exception as e:
            print(f"💥 通信エラー: {e}")
            return False

    def run_scan_with_tags(self, tags: Optional[List[str]] = None, mode: str = "or"):
        print("=== 精度を高めた神戸市不満投稿のフルスキャンを開始 ===")
        
        loop_count = 0
        max_safety_loops = max(len(self.stations), len(self.cities), len(self.negative_keywords)) * 2 + 50

        while loop_count < max_safety_loops:
            loop_count += 1
            print(f"\n--- スキャンステップ: {loop_count} / 最大 {max_safety_loops} ---")
            
            is_finished = self.fetch_posts_by_keyword(max_results=10)
            
            if is_finished:
                print("🎉 すべてのマスターデータの最後までスキャンし切りました！")
                break
                
            time.sleep(2)

        print("=== 全自動スキャンが完了しました ===")

if __name__ == "__main__":
    collector = XPostCollector()
    collector.run_scan_with_tags()