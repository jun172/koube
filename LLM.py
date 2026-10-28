from google.cloud import language_v1

def analyze_sentiment_kobe(text: str) -> dict:
    """
    Google Cloud Natural Language API を使用して感情分析を行い、
    -0.3 〜 -0.6 の「建設的不満ゾーン」以外はすべて排除（False）する関数
    """
    client = language_v1.LanguageServiceClient()

    document = language_v1.Document(
        content=text,
        type_=language_v1.Document.Type.PLAIN_TEXT,
        language="ja"
    )

    try:
        response = client.analyze_sentiment(request={"document": document})
        sentiment = response.document_sentiment

        score = float(sentiment.score)       
        magnitude = float(sentiment.magnitude) 

        # 🎯 【厳格な条件】 -0.3 〜 -0.6 の範囲以外はすべて排除する
        is_target_complaint_zone = (-0.6 <= score <= -0.3)

        # 範囲外のものはすべて「排除」ラベルにする
        if is_target_complaint_zone:
            label = "✅ 採用（建設的不満・改善要望）"
        else:
            label = "🚫 排除（対象外：範囲外スコア）"

        return {
            "text": text,
            "sentiment_score": round(score, 3),
            "sentiment_magnitude": round(magnitude, 3),
            "is_target_complaint": is_target_complaint_zone,
            "label": label
        }

    except Exception as e:
        print(f"❌ 感情分析エラー: {e}")
        return {
            "text": text,
            "sentiment_score": 0.0,
            "sentiment_magnitude": 0.0,
            "is_target_complaint": False,
            "label": "🚫 排除（エラー）"
        }


# ==========================================
# 🧪 テスト実行例
# ==========================================
if __name__ == "__main__":
    sample_texts = [
        "三ノ宮駅前のバス乗り場が分かりにくくて困る。案内板をもっと見やすくしてほしい。",
        "神戸市の夜景がすごく綺麗で最高でした！また来たいです！",
        "税金ばかり取って本当に最低な行政だ！今すぐ全員やめろ！"
    ]

    print("=== 厳格なセンチメント範囲フィルター（-0.3〜-0.6以外は排除）テスト ===\n")
    for sample in sample_texts:
        result = analyze_sentiment_kobe(sample)
        print(f"📝 投稿: {result['text']}")
        print(f"   📊 スコア: {result['sentiment_score']} | 感情の強さ: {result['sentiment_magnitude']}")
        print(f"   🎯 判定: {result['label']} (対象フラグ: {result['is_target_complaint']})")
        print("-" * 50)