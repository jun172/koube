import os
from typing import Dict, List, Optional
import google.generativeai as genai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Gemini APIの初期化
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# スパム判定用の学習済みモデル（簡易モック / 本番では事前にfitしたモデルを読み込み）
class SpamFilter:
    def __init__(self):
        self.vectorizer = TfidfVectorizer()
        self.classifier = LogisticRegression()
        # 初期学習データ（例）
        X_train = ["無料プレゼント応募はこちら", "副業で月30万円稼ぐ方法", "三ノ宮駅の混雑がひどくて困る", "市役所の対応を改善してほしい"]
        y_train = [1, 1, 0, 0] # 1: スパム/広告, 0: 正常な市民の声
        X_vec = self.vectorizer.fit_transform(X_train)
        self.classifier.fit(X_vec, y_train)

    def is_spam(self, text: str) -> bool:
        vec = self.vectorizer.transform([text])
        prediction = self.classifier.predict(vec)
        return bool(prediction[0] == 1)

spam_filter = SpamFilter()

def process_sns_post(post: Dict) -> Optional[Dict]:
    """
    SNS投稿データを受け取り、条件判定・スパム除外・Geminiによる感情分析・ツリー生成を行う
    """
    text = post.get("text", "")
    follower_count = post.get("follower_count", 0)
    likes_count = post.get("likes_count", 0)

    # 1. 5,000人以上のアカウント（インフルエンサーやメディア等）の投稿を除外する条件分岐
    if follower_count >= 5000:
        print(f"Skipped: フォロワー数超過 ({follower_count}人)")
        return None

    # 2. scikit-learnを用いた広告・スパム投稿の排除
    if spam_filter.is_spam(text):
        print(f"Skipped: スパム・広告判定 -> {text}")
        return None

    # 3. Gemini APIを用いた感情分析（ポジティブ / ネガティブ）とロジックツリー構造化
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        以下の市民からのSNS投稿を分析し、JSON形式で結果を返してください。
        投稿内容: "{text}"
        
        要件:
        1. 感情分析: "negative"（不満・課題）か "positive"（要望・賛同）かを判定し "sentiment" キーに格納する。
        2. Whatツリー: 課題や要望の要素分解（大分類・中分類・小分類）を "what_tree" キーにJSON構造（name, children）で格納する。
        """
        
        # 構造化出力やJSONパースを前提としたプロンプト実行
        response = model.generate_content(prompt)
        
        # 分析結果の統合
        analyzed_result = {
            "text": text,
            "likes_count": likes_count,
            "follower_count": follower_count,
            "ai_analysis": response.text # 実際にはJSONパースして格納
        }
        
        return analyzed_result

    except Exception as e:
        print(f"Gemini API Error: {e}")
        return None