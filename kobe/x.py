import os
import time
import requests
from datetime import datetime, timezone
from typing import List, Tuple, Optional
from database import get_supabase_client
from dotenv import load_dotenv
import pandas as pd

load_dotenv()  

X_API_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN")
SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/all"

class KobeMultiCSVAnalyzer:
    def __init__(
        self, 
        kobe_csv: str = "kobe.csv", 
        stations_csv: str = "kobe_stations.csv", 
        sentiment_csv: str = "kobe_sentiment_keywords.csv",
        master_csv: str = "kobe_keywords_master.csv",
        life_csv: str = "kobe_life_keywords.csv",
        youth_csv: str = "kobe_youth_sentiment_keywords.csv"
    ):
        self.headers = {
            "Authorization": f"Bearer {X_API_BEARER_TOKEN}"
        }
        self.supabase = get_supabase_client()
        
        # --- 各種CSVからデータを完全ロード ---
        self.cities = self._load_csv_column(kobe_csv, 'city')
        self.towns = self._load_csv_column(kobe_csv, 'town')
        self.stations = self._load_stations_csv(stations_csv) # ヘッダーなし駅名用ローダー
        
        # 感情・ネガティブ系キーワード
        _, self.negative_keywords = self._load_sentiments(sentiment_csv)
        
        # 追加の専門・ライフ・若者向けCSVのロード
        self.master_keywords = self._load_general_csv(master_csv)
        self.life_keywords = self._load_general_csv(life_csv)
        self.youth_keywords = self._load_general_csv(youth_csv)

        print(f"📌 CSVロード完了:")
        print(f"  - 行政区: {len(self.cities)}件 / 町名: {len(self.towns)}件 / 駅名: {len(self.stations)}件")
        print(f"  - ネガティブ: {len(self.negative_keywords)}件 / マスター: {len(self.master_keywords)}件")
        print(f"  - ライフ関連: {len(self.life_keywords)}件 / 若者向け: {len(self.youth_keywords)}件")

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
        except Exception:
            pass
        return items

    def _load_stations_csv(self, path: str) -> List[str]:
        """ヘッダーのない駅名CSVを安全に読み込む"""
        items = []
        try:
            df = pd.read_csv(path, header=None)
            target_col = df.columns[0]
            for _, row in df.iterrows():
                val = str(row.get(target_col, '')).strip()
                if val and val != "nan" and val not in items:
                    items.append(val)
        except Exception:
            pass
        return items

    def _load_sentiments(self, path: str) -> Tuple[List[str], List[str]]:
        pos, neg = [], []
        try:
            df = pd.read_csv(path)
            if 'negative_keyword' in df.columns:
                neg = [str(x).strip() for x in df['negative_keyword'].dropna() if str(x).strip() != "nan"]
        except Exception:
            pass
        return pos, neg

    def _load_general_csv(self, path: str) -> List[str]:
        items = []
        try:
            df = pd.read_csv(path)
            target_col = None
            for col in df.columns:
                if any(k in col.lower() for k in ['keyword', 'name', 'text', 'word', 'term']):
                    target_col = col
                    break
            if not target_col and len(df.columns) > 0:
                target_col = df.columns[0]
                
            if target_col:
                for val in df[target_col].dropna():
                    s = str(val).strip()
                    if s and s != "nan" and s not in items:
                        items.append(s)
        except Exception:
            pass
        return items

    def _save_tweets_to_supabase(self, tweets: List[dict]) -> int:
        """取得したツイートを重複チェックしながらSupabaseに保存する"""
        saved_count = 0
        for tweet in tweets:
            metrics = tweet.get("public_metrics", {})
            post_data = {
                "platform": "X",
                "original_post_id": str(tweet.get("id")),
                "post_text": tweet.get("text"),
                "likes": metrics.get("like", 0),
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
        return saved_count

    def fetch_and_analyze_or_target(self, target_total: int = 1000):
        print(f"\n=== 【OR分析】目標 {target_total} 件のデータ収集を開始 ===")
        
        # 地域（行政区 ＋ 駅名）の結合
        area_pool = self.cities[:10] + self.stations[:10]
        area_query = " OR ".join([f'"{a}"' for a in area_pool]) if area_pool else '"神戸市"'
        
        # 不満・ライフ・若者キーワードの結合
        sentiment_pool = list(set(self.negative_keywords + self.life_keywords + self.youth_keywords))
        sentiment_query = " OR ".join([f'"{s}"' for s in sentiment_pool[:15]]) if sentiment_pool else '"不満"'
        
        query = f'(神戸市 OR {area_query}) ({sentiment_query}) -is:retweet lang:ja'
        print(f"🔍 ORクエリ: {query}")

        total_fetched = 0
        next_token = None
        
        while total_fetched < target_total:
            # 1回のリクエストで取得する上限（Search All APIの最大は通常100）
            fetch_limit = min(100, target_total - total_fetched)
            
            params = {
                "query": query,
                "max_results": fetch_limit,
                "tweet.fields": "created_at,public_metrics,author_id",
            }
            if next_token:
                params["next_token"] = next_token
            
            try:
                response = requests.get(SEARCH_ENDPOINT, headers=self.headers, params=params)
                if response.status_code == 429:
                    print("⚠️ レート制限に達しました。15分間待機します...")
                    time.sleep(900)
                    continue
                elif response.status_code != 200:
                    print(f"❌ X API Error: {response.status_code}\n{response.text}")
                    break
                
                data = response.json()
                tweets = data.get("data", [])
                
                if not tweets:
                    print("ℹ️ これ以上取得できる新しいツイートがありません。")
                    break
                
                saved = self._save_tweets_to_supabase(tweets)
                total_fetched += len(tweets)
                print(f"📥 累計取得件数: {total_fetched}件 (今回新規保存: {saved}件)")
                
                # 次のページのトークンを取得
                next_token = data.get("meta", {}).get("next_token")
                if not next_token:
                    print("ℹ️ ページネーションの終端に達しました。")
                    break
                
                time.sleep(1) # API負荷軽減のウェイト
                
            except Exception as e:
                print(f"💥 通信エラー: {e}")
                break
                
        print(f"🎉 OR分析による収集が完了しました。合計取得数: {total_fetched}件")

    def fetch_and_analyze_and_target(self, target_total: int = 1000):
        print(f"\n=== 【AND分析】目標 {target_total} 件の厳選データ収集を開始 ===")
        
        city = self.cities[0] if self.cities else "神戸市"
        station = self.stations[0] if self.stations else "三宮"
        neg = self.negative_keywords[0] if self.negative_keywords else "不満"
        
        query = f'"{city}" "{station}" "{neg}" -is:retweet lang:ja'
        print(f"🔍 ANDクエリ: {query}")

        total_fetched = 0
        next_token = None
        
        while total_fetched < target_total:
            fetch_limit = min(100, target_total - total_fetched)
            
            params = {
                "query": query,
                "max_results": fetch_limit,
                "tweet.fields": "created_at,public_metrics,author_id",
            }
            if next_token:
                params["next_token"] = next_token
            
            try:
                response = requests.get(SEARCH_ENDPOINT, headers=self.headers, params=params)
                if response.status_code == 429:
                    print("⚠️ レート制限に達しました。15分間待機します...")
                    time.sleep(900)
                    continue
                elif response.status_code != 200:
                    print(f"❌ X API Error: {response.status_code}\n{response.text}")
                    break
                
                data = response.json()
                tweets = data.get("data", [])
                
                if not tweets:
                    print("ℹ️ これ以上取得できる新しいツイートがありません。")
                    break
                
                saved = self._save_tweets_to_supabase(tweets)
                total_fetched += len(tweets)
                print(f"📥 AND累計取得件数: {total_fetched}件 (今回新規保存: {saved}件)")
                
                next_token = data.get("meta", {}).get("next_token")
                if not next_token:
                    print("ℹ️ ページネーションの終端に達しました。")
                    break
                
                time.sleep(1)
                
            except Exception as e:
                print(f"💥 通信エラー: {e}")
                break
                
        print(f"🎉 AND分析による収集が完了しました。合計取得数: {total_fetched}件")
    
    def run_scan_with_tags(self, tags: Optional[List[str]] = None, mode: str = "or"):
        """main.py からの呼び出しを受け付けるラッパーメソッド（1000件目標）"""
        print(f"=== 【x.py 連携】タグ収集開始 (モード: {mode.upper()}, タグ: {tags}) ===")
        if mode.lower() == "and":
            self.fetch_and_analyze_and_target(target_total=1000)
        else:
            self.fetch_and_analyze_or_target(target_total=1000)
    
if __name__ == "__main__":
    analyzer = KobeMultiCSVAnalyzer()
    # 1000件収集を実行する場合
    analyzer.fetch_and_analyze_or_target(target_total=1000)