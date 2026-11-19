import os
import json
from typing import Dict, Any, List
from dotenv import load_dotenv
from google.cloud import language_v1
from google import genai
from google.genai import types
import numpy as np

# database.py から Supabase 接続および保存関数をインポート[cite: 1, 4]
from database import *
from stop_flag import is_ai_stop_requested, reset_stop_ai

load_dotenv()  

# 各種クライアントの初期化
nl_client = language_v1.LanguageServiceClient()
genai_client = genai.Client()  # Gemini APIクライアント

# test_3.py で指定されたシステムプロンプト（AIへのルール指定）
SYSTEM_PROMPT = """あなたはSNSの投稿データを分析するデータアナリストです。
入力された投稿内容からスパムやノイズを判定し、政府・行政に対する国民の声や不満を適切に分類してください。

# 1. フィルタリング基準（is_valid の判定）
以下のいずれかに該当する場合は is_valid: false としてください。
- スパム・広告・勧誘・Bot投稿（アフィリエイトリンク、自動投稿、懸賞応募など）
- 意味をなさない文字列や暴言のみの投稿

# 2. 分類カテゴリ（category）※ is_valid が true の場合のみ指定
入力テキストがどの分野に関する声か、最も適切なものを1つ選んでください。
- 経済・物価（税金、物価高、給与、手当、景気など）
- 福祉・医療（年金、介護、医療費、子育て支援など）
- 外交・防衛（安全保障、外交政策、渡航関連など）
- インフラ・防災（交通、災害対応、環境、エネルギーなど）
- 政治・行政手続（選挙、議員の対応、行政のデジタル化・不祥事など）
- その他（上記に当てはまらない社会問題）

# 出力フォーマット
解説や前置き・挨拶は一切含めず、必ず以下のJSON配列（List）形式のみで出力してください。

[
  {
    "id": "ポストのID",
    "text": "入力テキスト内容",
    "is_valid": true,
    "category": "選択したカテゴリ名（is_validがfalseの場合はnull）"
  }
]
"""

class IntegratedAnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int, is_complaint: bool) -> int:
        """機械学習（NumPy）によるエンゲージメント考慮の優先度スコア計算"""
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        complaint_bonus = 15 if is_complaint else 0
        
        total_score = int((ai_base_priority * 0.6) + engagement_boost + complaint_bonus)
        return max(0, min(100, total_score))

    def analyze_and_classify_posts(self, limit: int = 50):
        """Supabaseから投稿を取得し、Google Cloud NL API + Gemini API + MLスコアリングを統合実行するメイン処理"""
        try:
            reset_stop_ai()
            
            # 1. Supabase の sns_posts テーブルからデータを取得
            response = self.supabase.table("sns_posts").select("*").order("collected_at", desc=True).limit(limit).execute()
            posts = response.data

            if not posts:
                print("sns_posts テーブルにデータがありません。")
                return

            print(f"📦 取得した投稿件数: {len(posts)}件。Gemini APIによる一括分類・スパム判定を実行中...")

            # 2. Gemini API でスパム判定 & カテゴリ分類を一括実行
            input_data = []
            for post in posts:
                if not isinstance(post, dict):
                    continue
                input_data.append({
                    "id": post.get("id"),
                    "text": str(post.get("post_text") or "")
                })

            input_text_json = json.dumps(input_data, ensure_ascii=False, indent=2)

            # Gemini API 呼び出し（JSON出力を強制）
            response_gemini = genai_client.models.generate_content(
                model='gemini-3.8-flash',  # 必要に応じてモデル名を調整
                contents=f"以下の入力テキスト（JSON形式）を分析し、指定のJSON配列形式で出力してください。\n\n# 入力テキスト\n{input_text_json}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            
            response_text = response_gemini.text if response_gemini.text else "{}"
            gemini_results = json.loads(response_text)
            gemini_map = {item.get("id"): item for item in gemini_results}

            processed_count = 0

            # 3. 各投稿ごとに Google Cloud NL API 感情分析 ＆ ML優先度計算 ＆ データベース保存
            for post in posts:
                if not isinstance(post, dict):
                    continue

                if is_ai_stop_requested():
                    print("🛑 ユーザーからの停止リクエストを検知したため、処理を中断します。")
                    break

                post_id_raw = post.get("id")
                post_id = int(post_id_raw) if isinstance(post_id_raw, (int, str)) and str(post_id_raw).isdigit() else str(post_id_raw)
                text = str(post.get("post_text") or "")
                
                gemini_info = gemini_map.get(post_id, {})
                is_valid = gemini_info.get("is_valid", True)
                category = gemini_info.get("category", "その他")

                # スパムやノイズと判定された場合はスキップ
                if not is_valid:
                    continue

                raw_likes = post.get("likes_count", 0)
                likes = int(str(raw_likes)) if raw_likes is not None else 0
                raw_replies = post.get("replies_count", 0)
                replies = int(str(raw_replies)) if raw_replies is not None else 0
                
                # --- Google Cloud Natural Language API による感情分析 ---
                document = language_v1.Document(
                    content=text, 
                    type_=language_v1.Document.Type.PLAIN_TEXT
                )
                sentiment_response = nl_client.analyze_sentiment(request={"document": document})
                sentiment = sentiment_response.document_sentiment
                cloud_sentiment_score = float(sentiment.score)       
                sentiment_magnitude = float(sentiment.magnitude)     
                
                # 建設的不満ゾーン (-0.3 ~ -0.6) の判定
                is_target_sentiment_zone = (-0.6 <= cloud_sentiment_score <= -0.3)
                is_complaint = is_target_sentiment_zone  
                
                negativity = max(0.0, -cloud_sentiment_score) 
                magnitude_factor = min(10.0, sentiment_magnitude)
                
                base_priority = int((negativity * 70) + (magnitude_factor * 3))
                if is_target_sentiment_zone:
                    base_priority += 15

                base_priority = max(10, min(100, base_priority))

                # 機械学習による最終優先度スコア算出[cite: 1]
                final_priority = self._calculate_priority_with_ml(
                    ai_base_priority=base_priority,
                    likes=likes,
                    replies=replies,
                    impressions=0,
                    is_complaint=is_complaint
                )

                # --- 4. Supabase への結果保存 ---[cite: 1]
                analysis_data = {
                    "sns_post_id": post_id,
                    "is_valid_issue": True,
                    "sentiment_score": cloud_sentiment_score,
                    "priority_score": final_priority
                }
                insert_ai_analysis(analysis_data)

                # 課題カタログへの保存と紐付け
                prefix_label = "[行政課題・不満]" if is_complaint else "[一般話題]"
                post_summary = f"{prefix_label} {text[:20]}... (カテゴリ:{category})"
                
                # ▼ ここに "post_text": text を追加して NOT NULL 制約エラーを解消 ▼
                catalog_data = {
                    "main_category": category,
                    "sub_category": "感情スコア: " + f"{cloud_sentiment_score:.2f}",
                    "detail_category": f"優先度: {final_priority}",
                    "ai_summary": post_summary,
                    "total_priority_score": final_priority,
                    "post_text": text  # ← 追加
                }
                
                catalog_res = upsert_issue_catalog(catalog_data)
                if catalog_res and hasattr(catalog_res, "data") and isinstance(catalog_res.data, list) and len(catalog_res.data) > 0:
                    first_row = catalog_res.data[0]
                    if isinstance(first_row, dict) and "id" in first_row:
                        raw_id = first_row["id"]
                        if raw_id is not None:
                            link_issue_and_post(issue_id=int(str(raw_id)), sns_post_id=int(post_id))

                processed_count += 1
                print(f"✅ ID: {post_id} | カテゴリ: {category} | 優先度: {final_priority}")

            print(f"\n-------------------\n処理完了！有効な投稿 {processed_count} 件の分析とデータベース保存を完了しました。\n-------------------")

        except Exception as e:
            print(f"統合処理中にエラーが発生しました: {e}")

if __name__ == "__main__":
    print("🤖 統合AI分析エンジンを起動します...\n")
    engine = IntegratedAnalysisEngine()
    engine.analyze_and_classify_posts(limit=50)