import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from google import genai

from sklearn.preprocessing import MinMaxScaler
import numpy as np

# database.py から必要な保存関数をインポートする
from database import get_supabase_client, insert_ai_analysis, upsert_issue_catalog, link_issue_and_post

# .env ファイルから環境変数を読み込む
load_dotenv()

# クライアントの初期化
client = genai.Client()

_engine = None

def get_ai_engine():
    global _engine
    if _engine is None:
        _engine = AnalysisEngine()
    return _engine

class AnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()
        # 整数値のタプルに修正するか、使っていない場合はこの行自体を削除してOKです
        self.scaler = MinMaxScaler(feature_range=(0, 1))

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int) -> int:
        """
        scikit-learnの処理概念や特徴量スケーリングを応用し、
        AIが算出した深刻度とエンゲージメント指標（いいね、コメント、インプレッション）を統合して優先度を算出する。
        """
        # エンゲージメントの生データを配列にする [likes, replies, impressions]
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        
        # 仮の基準値を用いた正規化（実運用では過去データの統計量等に合わせる）
        # 例として、いいね最大100、返信最大50、インプレッション最大10000を想定したスケーリング
        weights = np.array([0.4, 0.4, 0.2])  # 各指標の重み付け
        
        # 特徴量の重み付き合算値を算出
        engagement_score = np.dot(raw_features, weights)[0]
        
        # シグモイド関数やクリッピングで 0〜30点分のブースト値に変換
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        
        # AI評価（最大70点分）＋ エンゲージメント補正（最大30点分）
        total_score = int((ai_base_priority * 0.7) + engagement_boost)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0):
        """
        1件のSNS投稿をGeminiで要約・分析し、エンゲージメント指標をscikit-learn系ロジックで統合してDBへ保存する
        """
        try:
            prompt = f"""
            以下の市民の投稿を分析・要約し、必ず以下のJSON形式のみで返してください（余計な解説やマークダウン以外のテキストは不要です）。
            投稿内容: "{text}"
            いいね数: {like_count}
            返信数: {reply_count}
            インプレッション数: {impressions}
            
            要件:
            1. ai_summary: 投稿内容の簡潔な要約文（30文字〜50文字程度）
            2. sentiment_score: -1.0(最悪)〜1.0(最高)の感情スコア（数値）
            3. what_tree: 大分類、中分類、小分類を含むツリー構造 (name, children)
            4. base_priority: 0-100のテキスト単体での深刻度（整数）
            5. tags: 投稿内容を象徴する日本語のキーワード・ハッシュタグのリスト（例: ["#再開発", "#神戸市", "#市民の声"]。2〜4個程度）
            """
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            
            raw_text = response.text if response.text else ""
            raw_text = raw_text.strip()

            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"): 
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]

            data = json.loads(raw_text.strip())
            post_summary = data.get("ai_summary", text[:50])

            # AIが算出した基本深刻度を取得
            base_priority = data.get("base_priority", data.get("priority_score", 50))
            
            # scikit-learnベースのロジックでエンゲージメントを反映した最終優先度を計算
            final_priority = self._calculate_priority_with_ml(
                ai_base_priority=base_priority,
                likes=like_count,
                replies=reply_count,
                impressions=impressions
            )

            # 1. ai_analysis_results テーブルへ保存
            analysis_data = {
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": data.get("sentiment_score", 0.0),
                "priority_score": final_priority
            }
            insert_ai_analysis(analysis_data)

            # 2. 課題カタログ（issue_catalog）および中間テーブルへの保存
            self._save_to_catalog(
                post_id=post_id, 
                tree_data=data.get("what_tree", {}), 
                priority=final_priority, 
                summary_text=post_summary
            )
            
            print(f"Post ID: {post_id} のAI分析とMLエンゲージメント補正（優先度: {final_priority}）の保存が完了しました。")

        except Exception as e:
            print(f"AI Analysis Error (Post ID: {post_id}): {e}")

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

# インスタンス化
engine = AnalysisEngine()