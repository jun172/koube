import torch
from transformers import pipeline
from sentence_transformers import SentenceTransformer
import numpy as np

# ---------------------------------------------------------
# 0. M4 Mac (Apple Silicon) GPU (MPS) の利用確認と設定
# ---------------------------------------------------------
device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"🚀 使用デバイス: {device} (M4 Mac GPU処理)")

# ---------------------------------------------------------
# 1. ① Transformers: ゼロショット分類 ＆ 感情分析
# ---------------------------------------------------------
print("📦 分類モデルをロード中...")
# ゼロショット分類パイプライン（事前学習データを準備せずにカテゴリ分け）
zero_shot_classifier = pipeline(
    "zero-shot-classification",
    model="cyberagent/open-calm-7b",  # または日本語対応の軽量分類モデル
    device=device
)

# 感情分析パイプライン
# 感情分析パイプライン
# 感情分析パイプライン（行末に ignore を置く）
sentiment_analyzer = pipeline(  # type: ignore
    "sentiment-analysis", # type: ignore
    model="koheidk/bert-base-japanese-sentiment-uncorrected"
)

# ゼロショット分類パイプライン
zero_shot_classifier = pipeline(  # type: ignore
    "zero-shot-classification",
    model="cyberagent/open-calm-7b"
)
# ---------------------------------------------------------
# 2. ② Sentence-Transformers: 文章ベクトル化（クラスタリング用）
# ---------------------------------------------------------
print("📦 ベクトル化モデルをロード中...")
embedder = SentenceTransformer("cl-nagoya/sup-simcse-ja-base", device=device)

# ---------------------------------------------------------
# 3. テスト用 SNS投稿データ
# ---------------------------------------------------------
sns_posts = [
    "三宮のカフェでランチなう！クロワッサンが美味しかった😊",
    "三宮の再開発工事のせいで毎朝バスが遅れて本当に困る。市はもっと対策して。",
    "山手線の工事で通行止めになってて渋滞ひどい。神戸市の道路計画どうなってんの？",
    "今日はメリケンパークで散歩。天気が良くて海風が気持ちいい！",
    "市バスのダイヤ改正で便数が減って通勤ラッシュが地獄。復活させてほしい。"
]

candidate_labels = ["個人の日常", "神戸市の政策・インフラへの不満", "行政への感謝・好意"]

print("\n=================== 📊 AI解析結果 ===================")

analyzed_results = []

for post in sns_posts:
    # A. 分類（個人の日常 vs 政策批判）
    cls_res = zero_shot_classifier(post, candidate_labels)
    top_label = cls_res["labels"][0]
    top_score = cls_res["scores"][0]

    # B. 感情分析（ポジティブ / ネガティブ）
    sent_res = sentiment_analyzer(post)[0]
    sentiment_label = sent_res["label"]
    sentiment_score = sent_res["score"]

    # C. ベクトル化（埋め込み表現の生成）
    embedding = embedder.encode(post)

    analyzed_results.append({
        "post_text": post,
        "category": top_label,
        "category_confidence": top_score,
        "sentiment": sentiment_label,
        "sentiment_score": sentiment_score,
        "embedding": embedding
    })

    print(f"\n💬 投稿文: {post}")
    print(f" ├─ 🏷️ 分類: 【{top_label}】 (確信度: {top_score:.2f})")
    print(f" ├─ 💖 感情: {sentiment_label} (スコア: {sentiment_score:.2f})")
    print(f" └─ 📐 ベクトル次元数: {len(embedding)}")

# ---------------------------------------------------------
# 4. 類似不満投稿の検索・グループ化（コサイン類似度）
# ---------------------------------------------------------
print("\n=================== 🔍 政策不満投稿の類似度分析 ===================")

# 不満・批判と判定された投稿のみを抽出
complaint_posts = [item for item in analyzed_results if "不満" in item["category"]]

if len(complaint_posts) >= 2:
    target = complaint_posts[0]
    print(f"🎯 基準となる不満投稿: 「{target['post_text']}」\n")

    for other in complaint_posts[1:]:
        # コサイン類似度の計算
        vec1 = target["embedding"]
        vec2 = other["embedding"]
        similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

        print(f" 🔗 比較対象: 「{other['post_text']}」")
        print(f"    └─ 類似度: {similarity:.4f} {'(💡 似た内容の不満)' if similarity > 0.6 else '(別の話題)'}")