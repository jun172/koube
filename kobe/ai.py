import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

from sklearn.preprocessing import MinMaxScaler
import numpy as np

# database.py から必要な保存関数をインポートする
from database import *

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
        self.scaler = MinMaxScaler(feature_range=(0, 1))

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int) -> int:
        """
        scikit-learnの処理概念や特徴量スケーリングを応用し、
        AIが算出した深刻度とエンゲージメント指標を統合して優先度を算出する。
        """
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        
        total_score = int((ai_base_priority * 0.7) + engagement_boost)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0):
        """
        1件のSNS投稿をGeminiで要約・分析し、エンゲージメント指標を統合してDBへ保存する
        """
        try:
            prompt = f"""
            以下の市民の投稿を「What（何が起きているか）」に絞って要素分解し、JSON形式のみで返してください（解説やマークダウンは不要）。

            投稿内容: "{text}"

            要件:
            1. ai_summary: 30〜50文字程度の要約
            2. sentiment_score: -1.0〜1.0の感情スコア
            3. what_tree: 「対象」「問題」「影響」の階層を持つJSONオブジェクト（name と children）
            4. base_priority: 0〜100の深刻度
            5. tags: キーワードのリスト（2〜4個）
            """
            
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            response_text = response.text if response.text else "{}"
            data = json.loads(response_text)
            post_summary = data.get("ai_summary", text[:50])

            base_priority = data.get("base_priority", data.get("priority_score", 50))
            
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

    def analyze_posts_from_db(self):
        """
        sns_posts テーブルからデータを直接参照し、AI分析を実行するメソッド
        """
        try:
            response = self.supabase.table("sns_posts").select("*").execute()
            posts = response.data

            if not posts:
                print("sns_posts にデータがありません。")
                return

            for post in posts:
                # post が辞書型であることを安全に確認
                if not isinstance(post, dict):
                    continue

                post_id = post.get("id")
                text = str(post.get("post_text") or "")
                
                # 安全に数値化
                try:
                    likes = int(post.get("likes_count", 0) or 0) # type: ignore
                except (TypeError, ValueError):
                    likes = 0

                try:
                    replies = int(post.get("replies_count", 0) or 0) # type: ignore
                except (TypeError, ValueError):
                    replies = 0
                
                # ★ ここにあった「既存チェック (existing)」の処理を削除しました
                
                print(f"Post ID: {post_id} のAI分析を開始します...")
                
                self.generate_analysis(
                    post_id=post_id,
                    text=text,
                    like_count=likes,
                    reply_count=replies,
                    impressions=0
                )

        except Exception as e:
            print(f"DB Fetch & Analysis Error: {e}")

    def analyze_issue_catalog_aggregation(self):
        """
        issue_catalog に集まった課題や関連する複数の投稿をグループごとに集約し、
        さらに深いレベルの「都市全体の構造的課題・原因」をGeminiにメタ解析させる
        """
        try:
            # 1. カタログに登録されている課題をすべて取得
            response = self.supabase.table("issue_catalog").select("*").execute()
            catalogs = response.data

            if not catalogs:
                print("issue_catalog にデータがありません。")
                return

            for catalog in catalogs:
                if not isinstance(catalog, dict):
                    continue
                
                issue_id = catalog.get("id")
                main_cat = catalog.get("main_category")
                sub_cat = catalog.get("sub_category")
                summary = catalog.get("ai_summary")

                print(f"カテゴリ [{main_cat} / {sub_cat}] のメタ解析を実行中...")

                # 2. この課題に紐づくすべてのSNS投稿テキストを集める
                relations = self.supabase.table("issue_post_relations").select("sns_post_id").eq("issue_id", issue_id).execute()
                post_ids = [r["sns_post_id"] for r in relations.data if isinstance(r, dict) and "sns_post_id" in r]

                collected_texts = []
                if post_ids:
                    posts_res = self.supabase.table("sns_posts").select("post_text").in_("id", post_ids).execute()
                    collected_texts = [p["post_text"] for p in posts_res.data if isinstance(p, dict) and "post_text" in p]

                # 3. まとめたテキストを元に、Geminiに「さらに深い上位の要素分解（なぜなぜ分析・構造的要因）」を指示する
                meta_prompt = f"""
                以下は、市民から寄せられた「{main_cat}（{sub_cat}）」に関する複数の具体的な不満・意見です。
                これらの声をメタ解析（横断的分析）し、行政として取り組むべき「根本的な構造的要因」や「政策的背景」を深く要素分解してください。
                
                集約された意見・要約:
                - {summary}
                - デバッグ用生データ抜粋: {json.dumps(collected_texts[:5], ensure_ascii=False)}
                
                要件（必ずJSON形式のみで返してください）:
                1. structural_cause: この問題を引き起こしている根本的な制度的・構造的原因の要約（40〜60文字）
                2. policy_recommendation: 行政が取るべき具体的な改善策やアプローチの提案
                3. deeper_tree: さらに詳細な原因・影響・対策に分かれるロジックツリー構造 (name, children)
                """

                response = client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=meta_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                meta_data = json.loads(response.text if response.text else "{}")
                print(f"メタ解析完了: {meta_data.get('structural_cause')}")

                # 必要であれば、解析結果を issue_catalog の新しいカラム（例: structural_cause など）に保存・アップデートする
                self.supabase.table("issue_catalog").update({
                    "ai_summary": f"【構造的要因】{meta_data.get('structural_cause', summary)}"
                }).eq("id", issue_id).execute()

        except Exception as e:
            print(f"Meta Analysis Error: {e}")
        
# 実行用ブロック
if __name__ == "__main__":
    engine = get_ai_engine()
    engine.analyze_posts_from_db()