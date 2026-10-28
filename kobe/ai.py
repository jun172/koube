import os
import json
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from google.cloud import language_v1
from sklearn.preprocessing import MinMaxScaler
import numpy as np
import pandas as pd

from database import *

# 停止フラグのインポート
from stop_flag import is_ai_stop_requested, request_stop_ai, reset_stop_ai

load_dotenv()  

nl_client = language_v1.LanguageServiceClient()

_engine = None

def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = AnalysisEngine()
    return _engine


class AnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()
        self.scaler = MinMaxScaler(feature_range=(0, 1))

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
            # Google Cloud Natural Language API で感情分析を実行
            document = language_v1.Document(
                content=text, 
                type_=language_v1.Document.Type.PLAIN_TEXT
            )
            
            sentiment_response = nl_client.analyze_sentiment(request={"document": document})
            sentiment = sentiment_response.document_sentiment
            cloud_sentiment_score = float(sentiment.score)       
            sentiment_magnitude = float(sentiment.magnitude)     
            
            # 🎯 センチメントスコアが -0.3 ~ -0.6 の「建設的不満ゾーン」か判定
            is_target_sentiment_zone = (-0.6 <= cloud_sentiment_score <= -0.3)
            is_complaint = is_target_sentiment_zone  # この範囲なら不満・課題として扱う
            
            negativity = max(0.0, -cloud_sentiment_score) 
            magnitude_factor = min(10.0, sentiment_magnitude)
            
            base_priority = int((negativity * 70) + (magnitude_factor * 3))
            
            # ターゲット・センチメントゾーンに合致する場合は優先度にボーナスを付与
            if is_target_sentiment_zone:
                base_priority += 15

            base_priority = max(10, min(100, base_priority))

            top_entity = "神戸市民意見"
            prefix_label = "[行政課題・不満]" if is_complaint else "[一般話題]"
            post_summary = f"{prefix_label} {text[:20]}... (スコア:{cloud_sentiment_score:.2f})"
            
            what_tree = {
                "name": top_entity,
                "children": [
                    {
                        "name": "一般課題",
                        "children": [{"name": f"感情スコア: {cloud_sentiment_score:.2f} (ゾーン特化)"}]
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
            reset_stop_ai()
            
            response = self.supabase.table("sns_posts").select("*").order("collected_at", desc=True).execute()
            posts = response.data

            if not posts:
                print("sns_posts にデータがありません。")
                return

            for post in posts:
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