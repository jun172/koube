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

# 新しい SDK での Gemini クライアント初期化
gemini_client = genai.Client()

_engine = None

def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = AnalysisEngine()
    return _engine


class HybridRegionalClassifier:
    """
    キーワードマッチ（高速）と Gemini AI（文脈・関連性推論）を組み合わせたハイブリッド地域分類クラス
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
                city = str(row.get('city', '')).strip() 
                town = str(row.get('town', '')).strip() 
                
                if town and town != "nan" and "以下に掲載がない場合" not in town:
                    self.kobe_keywords.add(town)
                    self.ward_mapping[town] = city
                
                if city and city != "nan":
                    self.kobe_keywords.add(city)
                    self.ward_mapping[city] = city

            # 2. kobe_stations.csv（駅名）を読み込む
            df_stations = pd.read_csv(stations_csv)
            for _, row in df_stations.iterrows():
                station = str(row.get('station_name', '')).strip()
                if station and station != "nan":
                    self.kobe_keywords.add(station)
                    if station in ["三宮", "元町", "三ノ宮", "神戸", "高速神戸", "新神戸", "県庁前", "花隈", "ポートターミナル"]:
                        self.ward_mapping[station] = "神戸市中央区"
                    elif station in ["上沢", "新開地", "大倉山", "兵庫"]:
                        self.ward_mapping[station] = "神戸市兵庫区"
                    elif station in ["長田", "高速長田", "西代"]:
                        self.ward_mapping[station] = "神戸市長田区"
                    elif station in ["新長田"]:
                        self.ward_mapping[station] = "神戸市長田区"
                    else:
                        self.ward_mapping[station] = "神戸市中央区" 

            print(f"📌 正規マスタから地域・駅キーワード合計 {len(self.kobe_keywords)} 件をロードしました。")
        except Exception as e:
            print(f"⚠️ マスタ読み込みエラー: {e}")

    def classify_text(self, text: str) -> Dict[str, Any]:
        """
        1. キーワードマッチを試す
        2. ヒットした場合も、単なる個人の日常私的ツイート（無関係な話題）でないかをGeminiで厳密にチェックまたはAIフォールバックへ
        """
        # Step 1: キーワードマッチング
        matched_keyword = next((kw for kw in self.kobe_keywords if kw in text), None)
        
        # キーワードがヒットしても、それが「単に地名ワードが含まれているだけの無関係な個人の日常投稿」でないかを精査するため、
        # より高度にGeminiで神戸市との関連性を判定させる
        return self._classify_and_filter_with_gemini(text, matched_keyword)

    def _classify_and_filter_with_gemini(self, text: str, matched_keyword: Optional[str]) -> Dict[str, Any]:
        default_ward = self.ward_mapping.get(matched_keyword, "神戸市中央区") if matched_keyword else "神戸市中央区"
        default_area = matched_keyword if matched_keyword else "神戸市内"

        prompt = f"""
        以下のSNS投稿テキストが、**兵庫県神戸市の行政、地域課題、街の魅力、イベント、防災、交通、店舗・施設、あるいは神戸市民の生活に直結する話題**として適切か判定してください。

        【判定基準（重要）】
        - 単に地名や駅名（「神戸」など）が一文字含まれているだけで、実際には個人のプライベートな私事、無関係な日常の愚痴、宣伝スパム、または神戸と本質的な関係がない場合は **is_kobe_related: false** としてください。
        - 神戸の街、地域、行政、観光、イベント、交通情報、または神戸での生活に関する話題であれば **is_kobe_related: true** としてください。

        【テキスト】
        "{text}"

        【出力フォーマット】
        必ず以下のJSON形式のフォーマットでのみ回答してください（Markdownのバッククォート ```json などは付けず、純粋なJSON文字列のみを返してください）。

        {{
        "is_kobe_related": true または false,
        "ward": "神戸市〇〇区（例: 神戸市中央区, 神戸市北区 など。関連しない場合は null）",
        "matched_area": "詳細エリア名またはトピック（関連しない場合は null）"
        }}
        """
        try:
            if not gemini_client or not hasattr(gemini_client, "models"):
                raise Exception("Gemini client is not initialized properly.")

            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',  # 安定した軽量・高速モデル
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
            data["method"] = "gemini_strict_filter"
            
            # もしキーワードマッチしていて関連ありと判定された場合はキーワード側の区を優先補正
            if data.get("is_kobe_related") and matched_keyword:
                if not data.get("ward") or data.get("ward") == "null":
                    data["ward"] = default_ward
                if not data.get("matched_area") or data.get("matched_area") == "null":
                    data["matched_area"] = default_area
                    
            return data
        except Exception as e:
            print(f"Gemini Filtering Error: {e}")
            # エラー時のフォールバック（キーワードがあれば一応救済、なければ除外）
            if matched_keyword:
                return {
                    "is_kobe_related": True,
                    "ward": default_ward,
                    "matched_area": default_area,
                    "method": "fallback_keyword"
                }
            return {
                "is_kobe_related": False,
                "ward": None,
                "matched_area": None,
                "method": "fallback_reject"
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
            # 1. 厳密な関連性＆地域分類チェック
            region_info = self.regional_classifier.classify_text(text)
            
            # ★ 神戸に関係のない投稿（個人スパムや無関係なつぶやき）はここで完全に排除
            if not region_info.get("is_kobe_related", False):
                print(f"🚫 [除外] 神戸市と無関係な投稿と判定されたためスキップ [Post ID: {post_id}]: {text[:30]}...")
                return

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

            top_entity = matched_area if matched_area else "市民意見"
            post_summary = f"【{top_entity}】{text[:25]}..."
            
            sub_category = ward if ward else "一般課題"
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
            
            print(f"✨ [保存完了] Post ID: {post_id} [地域: {matched_area} / 区: {ward}]")

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
                    
                self.generate_analysis(
                    post_id=post_id,
                    text=text,
                    like_count=likes,
                    reply_count=replies,
                    impressions=0
                )

        except Exception as e:
            print(f"DB Fetch & Analysis Error: {e}")


if __name__ == "__main__":
    engine = get_ai_engine()
    engine.analyze_posts_from_db()