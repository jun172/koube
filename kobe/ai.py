import os
import json
from typing import Dict, Any
from dotenv import load_dotenv
from google import genai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from database import get_supabase_client

# .env ファイルから環境変数を読み込む
load_dotenv()

# クライアントの初期化（環境変数 GEMINI_API_KEY を自動読み込み）
client = genai.Client()

class AnalysisEngine:
    def __init__(self):
        self.supabase = get_supabase_client()
        # スパム判定用の学習済みモデルを初期化
        self.vectorizer = TfidfVectorizer()
        self.classifier = LogisticRegression()
        self._load_spam_model()

    def _load_spam_model(self):
        spam_samples = ["無料プレゼント", "副業で稼ぐ", "フォローしてね", "FX", "投資"]
        normal_samples = ["三宮駅の混雑がひどい", "市役所の対応が遅い", "道路のひび割れを直してほしい"]
        
        texts = spam_samples + normal_samples
        labels = [1] * len(spam_samples) + [0] * len(normal_samples)
        
        X = self.vectorizer.fit_transform(texts)
        self.classifier.fit(X, labels)

    def is_spam(self, text: str) -> bool:
        vec = self.vectorizer.transform([text])
        return self.classifier.predict(vec)[0] == 1

    def generate_analysis(self, post_id: int, text: str, like_count: int):
        if self.is_spam(text):
            print(f"Post {post_id} はスパムと判定されました。")
            return

        try:
            prompt = f"""
            以下の市民の投稿を分析し、JSON形式のみで返してください（余計な解説は不要です）。
            投稿内容: "{text}"
            いいね数: {like_count}
            
            要件:
            1. sentiment_score: -1.0(最悪)〜1.0(最高)の感情スコア（数値）
            2. what_tree: 大分類、中分類、小分類を含むツリー構造 (name, children)
            3. priority_score: 0-100の優先度（いいね数と深刻度で算出、整数）
            4. 市民が抱えている不満、課題、問題点に焦点を当ててロジックツリーを生成せよ
            """
            
            # 最新のSDKによるモデル呼び出し
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

            raw_text = raw_text.strip()
            data = json.loads(raw_text.strip())

            # DBへ解析結果を保存
            analysis_res = self.supabase.table("ai_analysis_results").insert({
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": data["sentiment_score"],
                "priority_score": data["priority_score"]
            }).execute()

            # 課題カタログ（issue_catalog）へのマージ保存を実装着手
            self._update_issue_catalog(post_id, data.get("what_tree", {}), data["priority_score"], text)

        except Exception as e:
            print(f"AI Analysis Error: {e}")

    def _update_issue_catalog(self, post_id: int, tree_data: Dict[str, Any], priority: int, original_text: str):
        """
        Whatツリーの階層構造から大・中・小カテゴリを抽出し、issue_catalog テーブルへ保存する。
        さらに issue_post_relations を通じて投稿とのリレーションを確立する。
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

            # 課題カタログへ挿入
            catalog_res = self.supabase.table("issue_catalog").insert({
                "main_category": main_cat,
                "sub_category": sub_cat,
                "detail_category": detail_cat,
                "ai_summary": original_text[:100] + ("..." if len(original_text) > 100 else ""),
                "total_priority_score": priority
            }).execute()

        # 安全にデータを取得して型エラーを回避する
            if catalog_res and hasattr(catalog_res, "data") and isinstance(catalog_res.data, list) and len(catalog_res.data) > 0:
                first_row = catalog_res.data[0]
                if isinstance(first_row, dict) and "id" in first_row:
                    issue_id = first_row["id"]
                    # 投稿と課題の中間テーブルへ紐付け
                    self.supabase.table("issue_post_relations").insert({
                        "issue_id": issue_id,
                        "sns_post_id": post_id
                    }).execute()

        except Exception as e:
            print(f"Issue Catalog Update Error: {e}")

# インスタンス化
engine = AnalysisEngine()