import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.cloud import language_v1

from sklearn.preprocessing import MinMaxScaler
import numpy as np

# database.py から必要な保存関数をインポートする
from database import *

# .env ファイルから環境変数を読み込む
load_dotenv()

# Gemini クライアントの初期化
gemini_client = genai.Client()

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
        # Cloud Natural Language API クライアントの初期化
        self.nl_client = language_v1.LanguageServiceClient()

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int) -> int:
        """
        scikit-learnの処理概念を応用し、AIが算出した深刻度とエンゲージメント指標を統合して優先度を算出する。
        """
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        
        total_score = int((ai_base_priority * 0.7) + engagement_boost)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0) -> bool:
        """
        【適材適所】
        - 感情スコア算出: Cloud Natural Language API (高精度な感情解析)
        - 要約・ロジックツリー構造化: Gemini API (柔軟なJSON生成・要素分解)
        成功した場合は True、API制限などのエラーで失敗した場合は False を返す。
        """
        try:
            document = language_v1.Document(
                content=text,
                type_=language_v1.Document.Type.PLAIN_TEXT,
                language="ja"
            )

            # 1. 【Natural Language API】正確な感情スコアの取得 (-1.0 ~ 1.0)
            sentiment_response = self.nl_client.analyze_sentiment(request={"document": document})
            sentiment_score = float(sentiment_response.document_sentiment.score)

            # 2. 【Gemini API】「What（何が起きているか）」の要素分解とJSONツリーの生成
            prompt = f"""
            以下の市民の投稿を「What（何が起きているか）」に絞って要素分解し、JSON形式のみで返してください（解説やマークダウンは不要）。

            投稿内容: "{text}"

            要件:
            1. ai_summary: 30〜50文字程度の要約
            2. what_tree: 「対象」「問題」「影響」の階層を持つJSONオブジェクト（name と children）
            3. base_priority: 0〜100の深刻度
            4. tags: キーワードのリスト（2〜4個）
            """
            
            gemini_response = gemini_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            response_text = gemini_response.text if gemini_response.text else "{}"
            data = json.loads(response_text)
            post_summary = data.get("ai_summary", text[:50])
            base_priority = data.get("base_priority", 50)
            
            final_priority = self._calculate_priority_with_ml(
                ai_base_priority=base_priority,
                likes=like_count,
                replies=reply_count,
                impressions=impressions
            )

            # 3. ai_analysis_results テーブルへ保存
            analysis_data = {
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": sentiment_score,
                "priority_score": final_priority
            }
            insert_ai_analysis(analysis_data)

            # 4. 課題カタログ（issue_catalog）および中間テーブルへの保存
            self._save_to_catalog(
                post_id=post_id, 
                tree_data=data.get("what_tree", {}), 
                priority=final_priority, 
                summary_text=post_summary
            )
            
            print(f"Post ID: {post_id} の統合分析（NLP感情分析 + Gemini構造化）と優先度({final_priority})の保存が完了しました。")
            return True

        except Exception as e:
            err_msg = str(e).lower()
            # APIのクォータ超過・レートリミット（429エラーなど）の検知
            if "quota" in err_msg or "rate limit" in err_msg or "429" in err_msg or "resourceexhausted" in err_msg:
                print(f"【API制限エラー】APIが足りません（上限に達しました）。これ以上の処理を中断します。詳細: {e}")
                return False
            else:
                print(f"Analysis Error (Post ID: {post_id}): {e}")
                return True # 通常のエラーの場合は次の投稿へ継続

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
        sns_posts テーブルからまだAI分析が行われていない新しい投稿のみを抽出し、統合分析を実行する。
        API制限に達した場合はその場で安全に処理を中断する。
        """
        try:
            # 1. 既に分析済みの投稿IDリストを取得する（strを経由して安全にintにキャスト）
            analyzed_res = self.supabase.table("ai_analysis_results").select("sns_post_id").execute()
            analyzed_post_ids = {
                int(str(row["sns_post_id"])) 
                for row in (analyzed_res.data or []) 
                if isinstance(row, dict) and row.get("sns_post_id") is not None
            }

            # 2. sns_posts から全件取得
            response = self.supabase.table("sns_posts").select("*").execute()
            posts = response.data

            if not posts:
                print("sns_posts にデータがありません。")
                return

            # 3. 未処理（まだ分析結果がない）の投稿のみに絞り込む（strを経由して安全にintにキャスト）
            unprocessed_posts = [
                post for post in posts 
                if isinstance(post, dict) and int(str(post.get("id"))) not in analyzed_post_ids
            ]

            if not unprocessed_posts:
                print("新しく分析すべき未処理の投稿はありません。")
                return

            print(f"未処理の新規投稿 {len(unprocessed_posts)} 件の統合分析を開始します...")

            for post in unprocessed_posts:
                post_id = post.get("id")
                text = str(post.get("post_text") or "")
                
                # 型安全な数値化
                try:
                    raw_likes = post.get("likes_count", 0)
                    likes = int(str(raw_likes)) if raw_likes is not None else 0
                except (TypeError, ValueError):
                    likes = 0

                try:
                    raw_replies = post.get("replies_count", 0)
                    replies = int(str(raw_replies)) if raw_replies is not None else 0
                except (TypeError, ValueError):
                    replies = 0
                
                print(f"Post ID: {post_id} の統合分析を実行中...")
                
                # 実行結果を受け取る（API制限で失敗した場合は False が返り、ループを中断）
                success = self.generate_analysis(
                    post_id=post_id,
                    text=text,
                    like_count=likes,
                    reply_count=replies,
                    impressions=0
                )

                if not success:
                    print("【中断】API制限に達したため、残りの投稿の処理をストップします。")
                    break

            print("処理を終了しました。")

        except Exception as e:
            print(f"DB Fetch & Analysis Error: {e}")

    def analyze_issue_catalog_aggregation(self):
        """
        【Gemini APIの得意分野】
        複数投稿を横断的にメタ解析し、構造的要因や政策提言を文章生成する
        """
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
                main_cat = catalog.get("main_category")
                sub_cat = catalog.get("sub_category")
                summary = catalog.get("ai_summary")

                print(f"カテゴリ [{main_cat} / {sub_cat}] のメタ解析を実行中...")

                relations = self.supabase.table("issue_post_relations").select("sns_post_id").eq("issue_id", issue_id).execute()
                post_ids = [r["sns_post_id"] for r in relations.data if isinstance(r, dict) and "sns_post_id" in r]

                collected_texts = []
                if post_ids:
                    posts_res = self.supabase.table("sns_posts").select("post_text").in_("id", post_ids).execute()
                    collected_texts = [p["post_text"] for p in posts_res.data if isinstance(p, dict) and "post_text" in p]

                meta_prompt = f"""
                以下は、市民から寄せられた「{main_cat}（{sub_cat}）」に関する複数の具体的な不満・意見です。
                これらの声をメタ解析（横断的分析）し、行政として取り組むべき「根本的な構造的要因」や「政策的背景」を深く要素分解してください。
                
                集約された意見・要約:
                - {summary}
                - デバッグ用生データ抜粋: 
                {json.dumps(collected_texts[:5], ensure_ascii=False)}
                
                要件（必ずJSON形式のみで返してください）:
                1. structural_cause: この問題を引き起こしている根本的な制度的・構造的原因の要約（40〜60文字）
                2. policy_recommendation: 行政が取るべき具体的な改善策やアプローチの提案
                3. deeper_tree: さらに詳細な原因・影響・対策に分かれるロジックツリー構造 (name, children)
                """

                gemini_response = gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=meta_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                meta_data = json.loads(gemini_response.text if gemini_response.text else "{}")
                print(f"メタ解析完了: {meta_data.get('structural_cause')}")

                self.supabase.table("issue_catalog").update({
                    "ai_summary": f"【構造的要因】{meta_data.get('structural_cause', summary)}"
                }).eq("id", issue_id).execute()

        except Exception as e:
            print(f"Meta Analysis Error: {e}")
    
def analyze_sentiment_and_summary(text: str) -> dict:
    """
    検索時などの軽量処理: Cloud Natural Language APIで感情スコアを取得しつつ、簡易要約を返す
    """
    summary = text[:40] + ("..." if len(text) > 40 else "")
    sentiment_score = 0.0
    
    try:
        client = language_v1.LanguageServiceClient()
        document = language_v1.Document(
            content=text,
            type_=language_v1.Document.Type.PLAIN_TEXT,
            language="ja"
        )
        sentiment_response = client.analyze_sentiment(request={"document": document})
        sentiment_score = float(sentiment_response.document_sentiment.score)
    except Exception as e:
        print(f"NLP sentiment analysis error: {e}")
    
    return {
        "summary": summary,
        "sentiment_score": sentiment_score
    }

# 実行用ブロック
if __name__ == "__main__":
    engine = get_ai_engine()
    engine.analyze_posts_from_db()