import os
import json
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
# ★ Google Cloud Natural Language API のインポート
from google.cloud import language_v1
# ★ 新しい Google GenAI SDK のインポート
from google import genai

from sklearn.preprocessing import MinMaxScaler
import numpy as np
import pandas as pd
import re

# database.py から必要な保存関数をインポートする
from database import *

# .env ファイルから環境変数を読み込む
load_dotenv()

# Google Cloud Natural Language クライアントの初期化
nl_client = language_v1.LanguageServiceClient()

# 新しい SDK での Gemini クライアント初期化（自動で GEMINI_API_KEY を読み込みます）
gemini_client = genai.Client()

_engine = None

def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = AnalysisEngine()
    return _engine


class HybridRegionalClassifier:
    """
    キーワードマッチ（高速）と Gemini AI（文脈推論）を組み合わせたハイブリッド地域分類クラス
    """
    def __init__(self, master_csv: str = "kobe_keywords_master.csv"):
        self.kobe_keywords = set()
        self.ward_mapping = {}
        self._load_master(master_csv)

    def _load_master(self, kobe_csv: str = "kobe.csv", stations_csv: str = "kobe_stations.csv"):
        self.ward_mapping = {} # キーワード -> 区名 の辞書
        self.kobe_keywords = set()

        try:
            # 1. kobe.csv（行政区と町名）を読み込む
            df_kobe = pd.read_csv(kobe_csv)
            for _, row in df_kobe.iterrows():
                city = str(row.get('city', '')).strip() # 例: 神戸市東灘区
                town = str(row.get('town', '')).strip() # 例: 魚崎中町
                
                if town and town != "nan" and "以下に掲載がない場合" not in town:
                    # 町名キーワードを登録
                    self.kobe_keywords.add(town)
                    self.ward_mapping[town] = city
                
                if city and city != "nan":
                    # 区名自体も登録
                    self.kobe_keywords.add(city)
                    self.ward_mapping[city] = city

            # 2. kobe_stations.csv（駅名）を読み込む
            df_stations = pd.read_csv(stations_csv)
            for _, row in df_stations.iterrows():
                station = str(row.get('station_name', '')).strip()
                if station and station != "nan":
                    self.kobe_keywords.add(station)
                    # 駅名から所属する区を自動推測・マッピング（例: 三宮・元町なら中央区、上沢なら兵庫区など）
                    if station in ["三宮", "元町", "三ノ宮", "神戸", "高速神戸", "新神戸", "県庁前", "花隈", "ポートターミナル"]:
                        self.ward_mapping[station] = "神戸市中央区"
                    elif station in ["上沢", "新開地", "大倉山", "兵庫"]:
                        self.ward_mapping[station] = "神戸市兵庫区"
                    elif station in ["長田", "高速長田", "西代"]:
                        self.ward_mapping[station] = "神戸市長田区"
                    elif station in ["新長田"]:
                        self.ward_mapping[station] = "神戸市長田区" # （または一部長田/須磨）
                    else:
                        # その他の駅も必要に応じて区を紐付ける、またはデフォルト中央区等に
                        self.ward_mapping[station] = "神戸市中央区" 

            print(f"📌 正規マスタから地域・駅キーワード合計 {len(self.kobe_keywords)} 件をロードしました。")
        except Exception as e:
            print(f"⚠️ マスタ読み込みエラー: {e}")

    def classify_text(self, text: str) -> Dict[str, Any]:
        """
        1. まずキーワードマッチを試す
        2. ヒットしなかった場合は Gemini AI に文脈から推測させる
        """
        # Step 1: キーワードマッチング
        matched_keyword = next((kw for kw in self.kobe_keywords if kw in text), None)
        if matched_keyword:
            ward = self.ward_mapping.get(matched_keyword, "神戸市内")
            return {
                "matched_area": matched_keyword,
                "ward": ward,
                "is_kobe_related": True,
                "method": "keyword"
            }
            
        # Step 2: キーワードにない場合は Gemini AI による文脈推測（フォールバック）
        return self._classify_with_gemini(text)

    def _classify_with_gemini(self, text: str) -> Dict[str, Any]:
        prompt = f"""
                    以下のSNS投稿テキストは、兵庫県神戸市に関する市民の意見や日常のつぶやきです。
                    テキスト内に地名が一切含まれていなくても、文脈、トピック、言葉遣いから、**神戸市内の9区（東灘区、灘区、兵庫区、長田区、須磨区、垂水区、北区、中央区、西区）のどこに関連が深いか**を必ず1つだけ選んで推測してください。

                    【テキスト】
                    "{text}"

                    【絶対条件】
                    1. 「その他」や「不明」という判定は一切禁止です。必ずいずれかの行政区を選んでください。
                    2. 特定の区が判断できない一般的な日常の話題や街のつぶやきの大部分は、最も中心地である「神戸市中央区」およびエリア名「三宮・元町周辺」として扱ってください。
                    3. 必ず以下のJSON形式のフォーマットでのみ回答してください（Markdownのバッククォート ```json などは付けず、純粋なJSON文字列のみを返してください）。

                    {{
                    "ward": "神戸市〇〇区（例: 神戸市中央区, 神戸市北区, 神戸市東灘区 など）",
                    "matched_area": "詳細エリア名またはトピック（例: 三宮, 元町, 三宮周辺の飲食店, 街の意見 など）",
                    "is_kobe_related": true
                    }}
                    """
        try:
            if not gemini_client or not hasattr(gemini_client, "models"):
                raise Exception("Gemini client is not initialized properly.")

            response = gemini_client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt,
            )
            
            if not response or not response.text:
                raise Exception("Empty response from Gemini")

            clean_text = response.text.strip()
            
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
                
            data = json.loads(clean_text.strip())
            data["method"] = "gemini"
            return data
        except Exception as e:
            print(f"Gemini Classification Error: {e}")
            # エラー時も「その他」にせず中央区・三宮として救済する
            return {
                "matched_area": "三宮・周辺エリア",
                "ward": "神戸市中央区",
                "is_kobe_related": True,
                "method": "fallback"
            }

class AnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.regional_classifier = HybridRegionalClassifier()

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int) -> int:
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        
        total_score = int((ai_base_priority * 0.7) + engagement_boost)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0):
        try:
            region_info = self.regional_classifier.classify_text(text)
            matched_area = region_info.get("matched_area")
            ward = region_info.get("ward")

            document = language_v1.Document(
                content=text, 
                type_=language_v1.Document.Type.PLAIN_TEXT
            )
            
            sentiment_response = nl_client.analyze_sentiment(request={"document": document})
            sentiment = sentiment_response.document_sentiment
            cloud_sentiment_score = float(sentiment.score)       
            sentiment_magnitude = float(sentiment.magnitude)     

            entities_response = nl_client.analyze_entities(request={"document": document})
            extracted_entities = [entity.name for entity in entities_response.entities]
            
            negativity = max(0.0, -cloud_sentiment_score) 
            magnitude_factor = min(10.0, sentiment_magnitude)
            
            base_priority = int((negativity * 70) + (magnitude_factor * 3))
            base_priority = max(10, min(100, base_priority))

            top_entity = matched_area if matched_area else (extracted_entities[0] if extracted_entities else "市民意見")
            post_summary = f"【{top_entity}】{text[:25]}..."
            
            sub_category = ward if ward else (extracted_entities[1] if len(extracted_entities) > 1 else "一般課題")
            what_tree = {
                "name": top_entity,
                "children": [
                    {
                        "name": sub_category,
                        "children": [{"name": f"感情スコア: {cloud_sentiment_score:.2f} (熱量: {sentiment_magnitude:.1f})"}]
                    }
                ]
            }

            final_priority = self._calculate_priority_with_ml(
                ai_base_priority=base_priority,
                likes=like_count,
                replies=reply_count,
                impressions=impressions
            )

            analysis_data = {
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": cloud_sentiment_score,
                "priority_score": final_priority
            }
            insert_ai_analysis(analysis_data)

            self._save_to_catalog(
                post_id=post_id, 
                tree_data=what_tree, 
                priority=final_priority, 
                summary_text=post_summary
            )
            
            print(f"Post ID: {post_id} [地域: {matched_area} / 区: {ward} (by {region_info.get('method')})] の分析・保存完了")

        except Exception as e:
            print(f"Cloud NL Analysis Error (Post ID: {post_id}): {e}")

    def _save_to_catalog(self, post_id: Any, tree_data: Dict[str, Any], priority: int, summary_text: str):
        try:
            main_cat = tree_data.get("name", "一般課題")
            sub_cat = ""
            detail_cat = ""
            
            children = tree_data.get("children", [])
            if children:
                sub_child = children[0]
                sub_cat = sub_child.get("name", "")
                sub_children = sub_child.get("children", [])
                if sub_children:
                    detail_cat = sub_children[0].get("name", "")

            catalog_data = {
                "main_category": main_cat,
                "sub_category": sub_cat,
                "detail_category": detail_cat,
                "ai_summary": summary_text,
                "total_priority_score": priority
            }
            
            catalog_res = upsert_issue_catalog(catalog_data)

            if catalog_res and hasattr(catalog_res, "data") and isinstance(catalog_res.data, list) and len(catalog_res.data) > 0:
                first_row = catalog_res.data[0]
                if isinstance(first_row, dict) and "id" in first_row:
                    raw_id = first_row["id"]
                    if raw_id is not None:
                        issue_id = int(str(raw_id))
                        link_issue_and_post(issue_id=issue_id, sns_post_id=post_id)
                        
        except Exception as e:
            print(f"Catalog Save Error: {e}")

    def analyze_posts_from_db(self):
        try:
            response = self.supabase.table("sns_posts").select("*").order("collected_at", desc=True).execute()
            posts = response.data

            if not posts:
                print("sns_posts にデータがありません。")
                return

            for post in posts:
                if not isinstance(post, dict):
                    continue

                post_id = post.get("id")
                text = str(post.get("post_text") or "")
            
                raw_likes = post.get("likes_count", 0)
                try:
                    likes = int(str(raw_likes)) if raw_likes is not None else 0
                except (TypeError, ValueError):
                    likes = 0

                raw_replies = post.get("replies_count", 0)
                try:
                    replies = int(str(raw_replies)) if raw_replies is not None else 0
                except (TypeError, ValueError):
                    replies = 0
                    
                print(f"Post ID: {post_id} の地域分類 & AI分析を開始します...")
                
                self.generate_analysis(
                    post_id=post_id,
                    text=text,
                    like_count=likes,
                    reply_count=replies,
                    impressions=0
                )

        except Exception as e:
            print(f"DB Fetch & Analysis Error: {e}")

    def analyze_town_breakdown(self):
        try:
            response = self.supabase.table("sns_posts").select("post_text").execute()
            posts = response.data
            if not posts:
                print("集計対象の投稿がありません。")
                return

            ward_counts = {}
            town_counts = {}

            for p in posts:
                if not isinstance(p, dict):
                    continue
                text = str(p.get("post_text") or "")
                
                res = self.regional_classifier.classify_text(text)
                
                if isinstance(res, dict):
                    ward = res.get("ward") or "その他・分類不能"
                    town = res.get("matched_area") or "その他・主要駅"
                else:
                    ward = "その他・分類不能"
                    town = "その他・主要駅"

                if str(ward) == "None" or not ward:
                    ward = "その他・分類不能"
                if str(town) == "None" or not town:
                    town = "その他・主要駅"

                ward_counts[ward] = ward_counts.get(ward, 0) + 1
                town_counts[town] = town_counts.get(town, 0) + 1

            print("\n📊 【行政区別 投稿件数集計】")
            for w, count in sorted(ward_counts.items(), key=lambda x: x[1], reverse=True):
                print(f"  - {w}: {count} 件")

            print("\n📍 【町名・詳細エリア別 投稿件数集計（上位）】")
            sorted_towns = sorted(town_counts.items(), key=lambda x: x[1], reverse=True)
            for t, count in sorted_towns[:10]:
                print(f"  - {t}: {count} 件")

        except Exception as e:
            print(f"Town Breakdown Analysis Error: {e}")
            
    def analyze_issue_catalog_aggregation(self):
        try:
            response = self.supabase.table("issue_catalog").select("*").execute()
            catalogs = response.data

            if not catalogs:
                print("issue_catalog にデータがありません。")
                return

            for catalog in catalogs:
                if not isinstance(catalog, dict):
                    continue
                
                issue_id = catalog.get("id")
                
                relations = self.supabase.table("issue_post_relations").select("sns_post_id").eq("issue_id", issue_id).execute()
                post_ids = [r["sns_post_id"] for r in relations.data if isinstance(r, dict) and "sns_post_id" in r]

                if post_ids:
                    analysis_res = self.supabase.table("ai_analysis_results").select("sentiment_score").in_("sns_post_id", post_ids).execute()
            
            scores = [
                float(str(a["sentiment_score"])) 
                for a in analysis_res.data 
                if isinstance(a, dict) and a.get("sentiment_score") is not None
            ]
            
            avg_sentiment = sum(scores) / len(scores) if scores else 0.0
        except Exception as e:
            print(f"Meta Analysis Error: {e}")


if __name__ == "__main__":
    engine = get_ai_engine()
    engine.analyze_posts_from_db()
    engine.analyze_town_breakdown()