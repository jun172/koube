import os
import json
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from google.cloud import language_v1
from google import genai
from sklearn.preprocessing import MinMaxScaler
import numpy as np
import pandas as pd
import re

from database import *

# 停止フラグのインポート
from stop_flag import is_ai_stop_requested, request_stop_ai, reset_stop_ai

load_dotenv()  

nl_client = language_v1.LanguageServiceClient()
gemini_client = genai.Client()

_engine = None

def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = AnalysisEngine()
    return _engine

class HybridRegionalClassifier:
    def __init__(self, master_csv: str = "kobe_keywords_master.csv"):
        self.kobe_keywords = set()
        self.ward_mapping = {}
        self._load_master(master_csv)

    def _load_master(self, kobe_csv: str = "kobe.csv", stations_csv: str = "kobe_stations.csv"):
        self.ward_mapping = {} 
        self.kobe_keywords = set()

        try:
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
        matched_keyword = next((kw for kw in self.kobe_keywords if kw in text), None)
        return self._classify_and_filter_with_gemini(text, matched_keyword)

    def _classify_and_filter_with_gemini(self, text: str, matched_keyword: Optional[str]) -> Dict[str, Any]:
        default_ward = self.ward_mapping.get(matched_keyword, "神戸市中央区") if matched_keyword else "神戸市中央区"
        default_area = matched_keyword if matched_keyword else "神戸市内"

        prompt = f"""
        あなたは「神戸市の地域課題・インフラ不満分析」の専門データアナリストです。
提供されたX（Twitter）の投稿を分析し、指定のフォーマット（JSON等）で結果を返してください。

【対象領域】
兵庫県神戸市（および直接関連する交通・行政インフラ）

【判定ルール：保存（is_target = true）とする条件】
以下の中身のある「神戸市に関連する不満・課題・意見」のみを true としてください。
1. 交通・インフラへの不満
2. 行政・生活環境への課題・不満
3. 地域経済・街づくりへの意見

【判定ルール：除外（is_target = false）とする厳格な条件】
・個人の日常・私的な感想・雑談
・通過しただけの個人的な記録

判定対象のテキスト: "{text}"
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
            data["method"] = "gemini_strict_filter"
            
            if data.get("is_kobe_related") and matched_keyword:
                if not data.get("ward") or data.get("ward") == "null":
                    data["ward"] = default_ward
                if not data.get("matched_area") or data.get("matched_area") == "null":
                    data["matched_area"] = default_area
                    
            return data
        except Exception as e:
            print(f"Gemini Filtering Error: {e}")
            if matched_keyword:
                return {
                    "is_kobe_related": True,
                    "post_type": "general",
                    "ward": default_ward,
                    "matched_area": default_area,
                    "method": "fallback_keyword"
                }
            return {
                "is_kobe_related": False,
                "post_type": None,
                "ward": None,
                "matched_area": None,
                "method": "fallback_reject"
            }


class AnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        self.regional_classifier = HybridRegionalClassifier()

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int, is_complaint: bool) -> int:
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        complaint_bonus = 15 if is_complaint else 0
        
        total_score = int((ai_base_priority * 0.6) + engagement_boost + complaint_bonus)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0):
        try:
            region_info = self.regional_classifier.classify_text(text)
            
            if not region_info.get("is_kobe_related", False):
                return

            post_type = region_info.get("post_type", "general")
            if post_type == "personal":
                return

            is_complaint = (post_type == "complaint")
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
            
            negativity = max(0.0, -cloud_sentiment_score) 
            magnitude_factor = min(10.0, sentiment_magnitude)
            
            base_priority = int((negativity * 70) + (magnitude_factor * 3))
            base_priority = max(10, min(100, base_priority))

            top_entity = matched_area if matched_area else "市民意見"
            prefix_label = "[不満・課題]" if is_complaint else "[一般話題]"
            post_summary = f"{prefix_label}【{top_entity}】{text[:20]}..."
            
            sub_category = ward if ward else "一般課題"
            what_tree = {
                "name": top_entity,
                "children": [
                    {
                        "name": sub_category,
                        "children": [{"name": f"感情スコア: {cloud_sentiment_score:.2f} (タイプ: {post_type})"}]
                    }
                ]
            }

            final_priority = self._calculate_priority_with_ml(
                ai_base_priority=base_priority,
                likes=like_count,
                replies=reply_count,
                impressions=impressions,
                is_complaint=is_complaint
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
            # 起動時に停止フラグをリセット
            reset_stop_ai()
            
            response = self.supabase.table("sns_posts").select("*").order("collected_at", desc=True).execute()
            posts = response.data

            if not posts:
                print("sns_posts にデータがありません。")
                return

            for post in posts:
                # ここでウェブサイトからのAI解析停止リクエストを毎回チェック
                if is_ai_stop_requested():
                    print("🛑 ユーザーからの停止リクエストを検知したため、AI一括解析を安全に中断します。")
                    break

                if not isinstance(post, dict):
                    continue

                post_id = post.get("id")
                text = str(post.get("post_text") or "")
            
                raw_likes = post.get("likes_count", 0)
                likes = int(str(raw_likes)) if raw_likes is not None else 0
                raw_replies = post.get("replies_count", 0)
                replies = int(str(raw_replies)) if raw_replies is not None else 0
                    
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