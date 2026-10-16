import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
# ★ Google Cloud Natural Language API のインポート
from google.cloud import language_v1

from sklearn.preprocessing import MinMaxScaler
import numpy as np

# database.py から必要な保存関数をインポートする
from database import *

# .env ファイルから環境変数を読み込む
load_dotenv()

# Google Cloud Natural Language クライアントの初期化
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

    def _calculate_priority_with_ml(self, ai_base_priority: int, likes: int, replies: int, impressions: int) -> int:
        raw_features = np.array([[float(likes), float(replies), float(impressions)]])
        weights = np.array([0.4, 0.4, 0.2])
        
        engagement_score = np.dot(raw_features, weights)[0]
        engagement_boost = min(30.0, float(engagement_score) * 0.5)
        
        total_score = int((ai_base_priority * 0.7) + engagement_boost)
        return max(0, min(100, total_score))

    def generate_analysis(self, post_id: Any, text: str, like_count: int, reply_count: int = 0, impressions: int = 0):
        try:
            # 1. Cloud Natural Language API による高度なテキスト解析
            document = language_v1.Document(
                content=text, 
                type_=language_v1.Document.Type.PLAIN_TEXT
            )
            
            # ① 感情分析（スコアとマグニチュード［熱量］を取得）
            sentiment_response = nl_client.analyze_sentiment(request={"document": document})
            sentiment = sentiment_response.document_sentiment
            cloud_sentiment_score = float(sentiment.score)       # -1.0 (ネガティブ) 〜 1.0 (ポジティブ)
            sentiment_magnitude = float(sentiment.magnitude)     # 感情の強さ・熱量 (0.0 〜 Inf)

            # ② エンティティ分析（地名、組織、固有名詞などを抽出）
            entities_response = nl_client.analyze_entities(request={"document": document})
            extracted_entities = [entity.name for entity in entities_response.entities]
            
            # 2. CNL APIの分析結果から独自の指標を自動構築（Geminiの代わり）
            # ネガティブ度が高いほど、また感情の熱量が高いほど優先度（深刻度）を高く設定
            negativity = max(0.0, -cloud_sentiment_score) # 0.0 〜 1.0
            magnitude_factor = min(10.0, sentiment_magnitude)
            
            # 0〜100のベース優先度を算出
            base_priority = int((negativity * 70) + (magnitude_factor * 3))
            base_priority = max(10, min(100, base_priority))

            # 要約・見出しの自動生成（抽出されたエンティティやテキストを活用）
            top_entity = extracted_entities[0] if extracted_entities else "市民意見"
            post_summary = f"【{top_entity}】{text[:25]}..."
            
            # ロジックツリー（what_tree）の自動構築（エンティティ構造を反映）
            sub_category = extracted_entities[1] if len(extracted_entities) > 1 else "一般課題"
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

            # 3. ai_analysis_results テーブルへ保存
            analysis_data = {
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": cloud_sentiment_score,
                "priority_score": final_priority
            }
            insert_ai_analysis(analysis_data)

            # 4. 課題カタログ（issue_catalog）および中間テーブルへの保存
            self._save_to_catalog(
                post_id=post_id, 
                tree_data=what_tree, 
                priority=final_priority, 
                summary_text=post_summary
            )
            
            print(f"Post ID: {post_id} のCloud NL分析とML補正（優先度: {final_priority}）の保存が完了しました。")

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
                    
                print(f"Post ID: {post_id} のCloud NL分析を開始します...")
                
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
                
                # 関連する投稿の感情スコアを集計してメタ分析の代わりとする
                relations = self.supabase.table("issue_post_relations").select("sns_post_id").eq("issue_id", issue_id).execute()
                post_ids = [r["sns_post_id"] for r in relations.data if isinstance(r, dict) and "sns_post_id" in r]

                if post_ids:
                    analysis_res = self.supabase.table("ai_analysis_results").select("sentiment_score").in_("sns_post_id", post_ids).execute()
            
            # ★ 確実に float にキャストしてリスト内包表記にする
            # ★ str() を挟むことで Pylance の JSON 型エラーを完全に回避
            scores = [
                float(str(a["sentiment_score"])) 
                for a in analysis_res.data 
                if isinstance(a, dict) and a.get("sentiment_score") is not None
            ]
            
            avg_sentiment = sum(scores) / len(scores) if scores else 0.0
        except Exception as e:
            print(f"Meta Analysis Error: {e}")
    
def analyze_sentiment_and_summary(text: str) -> dict:
    summary = text[:30] + ("..." if len(text) > 30 else "")
    sentiment_score = 0.0
    
    try:
        document = language_v1.Document(
            content=text,
            type_=language_v1.Document.Type.PLAIN_TEXT
        )
        sentiment_response = nl_client.analyze_sentiment(request={"document": document})
        sentiment_score = float(sentiment_response.document_sentiment.score)
    except Exception as e:
        print(f"Realtime Sentiment Analysis Error: {e}")
    
    return {
        "summary": summary,
        "sentiment_score": sentiment_score
    }
class LocalTextClassifier:
    def __init__(self):
        # TF-IDF（単語の重要度計算）とロジスティック回帰をパイプライン化
        self.model = Pipeline([
            ('tfidf', TfidfVectorizer(max_features=1000, token_pattern=r'(?u)\b\w+\b')),
            ('clf', LogisticRegression(random_state=42))
        ])
        self.is_trained = False

    def train(self, texts: list, labels: list):
        """
        少量の教師データでモデルを学習させる
        texts: 投稿文のリスト
        labels: ラベルのリスト (例: "USEFUL", "NOISE")
        """
        if not texts:
            return
        self.model.fit(texts, labels)
        self.is_trained = True
        print(f"軽量MLモデルの学習が完了しました（学習データ数: {len(texts)}件）")

    def predict(self, text: str) -> str:
        """
        新しいテキストのカテゴリを瞬時に予測する
        """
        if not self.is_trained:
            return "UNKNOWN"
        return str(self.model.predict([text])[0])

# --- 使い方・動作イメージ ---
if __name__ == "__main__":
    classifier = LocalTextClassifier()
    
    # 仮の学習データ（実際には過去のデータや手動ラベル付けデータを使用）
    sample_texts = [
        "神戸市営地下鉄のダイヤ改正が不便すぎて最悪。改善してほしい。",
        "今日のランチは三宮のカフェでパンケーキを食べました！", # ノイズ
        "三宮駅前の再開発、工事長すぎて歩きにくいし危険。",
        "おはようございます！今日もいい天気ですね。", # ノイズ
    ]
    sample_labels = ["USEFUL", "NOISE", "USEFUL", "NOISE"]
    
    # 学習の実行
    classifier.train(sample_texts, sample_labels)
    
    # 新しい投稿の判定テスト
    new_post = "バスが全然来なくて遅刻した、神戸市どうにかして"
    result = classifier.predict(new_post)
    print(f"判定結果: {result} -> 「{new_post}」")
if __name__ == "__main__":
    engine = get_ai_engine()
    engine.analyze_posts_from_db()