import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from google import genai
from database import get_supabase_client

# .env ファイルから環境変数を読み込む
load_dotenv()

# クライアントの初期化（環境変数 GEMINI_API_KEY を自動読み込み）
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

    def generate_analysis(self, post_id: int, text: str, like_count: int):
        """
        1件のSNS投稿をGeminiで要約・分析し、ai_analysis_resultsとissue_catalogに保存する
        """
        try:
            
            prompt = f"""
            以下の市民の投稿を分析・要約し、必ず以下のJSON形式のみで返してください（余計な解説やマークダウン以外のテキストは不要です）。
            投稿内容: "{text}"
            いいね数: {like_count}
            
            要件:
            1. summary: 投稿内容の簡潔な要約文（30文字〜50文字程度）
            2. sentiment_score: -1.0(最悪)〜1.0(最高)の感情スコア（数値）
            3. what_tree: 大分類、中分類、小分類を含むツリー構造 (name, children)
            4. priority_score: 0-100の優先度（いいね数と深刻度で算出、整数）
            5. tags: 投稿内容を象徴する日本語のキーワード・ハッシュタグのリスト（例: ["#再開発", "#神戸市", "#市民の声"]。2〜4個程度）
            """
            
            response = client.models.generate_content(
                model="gemini-3.6-flash",
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
            post_summary = data.get("summary", text[:50])

            # 1. sns_posts 側にも要約を反映させたい場合はここで更新（必要に応じて）
            data = json.loads(raw_text.strip())
            post_summary = data.get("summary", text[:50])

            # ★ sns_posts テーブルの summary カラムを要約文で更新する
            self.supabase.table("sns_posts").update({
                "summary": post_summary
            }).eq("id", post_id).execute()

            # DBへ解析結果を保存 (sns_post_id の UNIQUE制約に対応)

            # 2. DBへ解析結果を保存 (sns_post_id の UNIQUE制約に対応)
            self.supabase.table("ai_analysis_results").upsert({
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": data.get("sentiment_score", 0.0),
                "priority_score": data.get("priority_score", 0)
            }, on_conflict="sns_post_id").execute()

            # 3. 課題カタログ（issue_catalog）へ保存
            self._update_issue_catalog(post_id, data.get("what_tree", {}), data.get("priority_score", 0), post_summary)
            print(f"Post {post_id} の要約・AI分析と保存が完了しました。要約: {post_summary}")

        except Exception as e:
            print(f"AI Analysis Error (Post ID: {post_id}): {e}")

    def _update_issue_catalog(self, post_id: int, tree_data: Dict[str, Any], priority: int, summary_text: str):
        """
        Whatツリーの階層構造から大・中・小カテゴリを抽出し、issue_catalog テーブルへ保存する。
        """
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

            # 課題カタログへ挿入（AIによる要約文を ai_summary に格納）
            catalog_res = self.supabase.table("issue_catalog").insert({
                "main_category": main_cat,
                "sub_category": sub_cat,
                "detail_category": detail_cat,
                "ai_summary": summary_text,
                "total_priority_score": priority
            }).execute()

            if catalog_res and hasattr(catalog_res, "data") and isinstance(catalog_res.data, list) and len(catalog_res.data) > 0:
                first_row = catalog_res.data[0]
                if isinstance(first_row, dict) and "id" in first_row:
                    issue_id = first_row["id"]
                    self.supabase.table("issue_post_relations").upsert({
                        "issue_id": issue_id,
                        "sns_post_id": post_id
                    }, on_conflict="issue_id,sns_post_id").execute()

        except Exception as e:
            print(f"Issue Catalog Update Error: {e}")

    def process_sns_posts_batch(self):
        try:
            analyzed_res = self.supabase.table("ai_analysis_results").select("sns_post_id").execute()
            
            analyzed_ids = []
            if analyzed_res and analyzed_res.data:
                analyzed_ids = [row["sns_post_id"] for row in analyzed_res.data if isinstance(row, dict) and "sns_post_id" in row]

            posts_res = self.supabase.table("sns_posts").select("*").execute()
            
            if not posts_res or not posts_res.data:
                print("処理対象の投稿がありません。")
                return

            posts = posts_res.data
            print(f"総投稿数: {len(posts)}件, 既解析数: {len(analyzed_ids)}件")

            for post in posts:
                if not isinstance(post, dict):
                    continue
                
                post_id = post.get("id")
                if post_id is None or post_id in analyzed_ids:
                    continue  

                post_text = post.get("post_text", "")
                likes_count = post.get("likes_count", 0)

                print(f"Analyzing & Summarizing Post ID {post_id}...")
                self.generate_analysis(
                    post_id=int(post_id),
                    text=str(post_text),
                    like_count=int(likes_count) if likes_count is not None else 0
                )

        except Exception as e:
            print(f"Batch Processing Error: {e}")

# インスタンス化
engine = AnalysisEngine()

if __name__ == "__main__":
    engine.process_sns_posts_batch()