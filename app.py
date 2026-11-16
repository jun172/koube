import os
import time
from dotenv import load_dotenv
from google import genai
import tweepy

# ==========================================
# 1. .env ファイルの読み込みと環境変数の取得
# ==========================================
load_dotenv()  # カレントディレクトリの .env を自動読み込み

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("Natural_Language")
X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_BEARER_TOKEN")
X_ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("ACCESS_TOKEN_SECRET")

# キーがセットされているかチェック
missing_keys = []
if not GEMINI_API_KEY:
  missing_keys.append("GEMINI_API_KEY (または Natural_Language)")
if not X_API_KEY:
  missing_keys.append("X_API_KEY")
if not X_API_SECRET:
  missing_keys.append("X_API_SECRET")
if not X_ACCESS_TOKEN:
  missing_keys.append("ACCESS_TOKEN")
if not X_ACCESS_TOKEN_SECRET:
  missing_keys.append("ACCESS_TOKEN_SECRET")

if missing_keys:
  raise ValueError(
      f"❌ .env ファイルに以下のAPIキーが不足しています: {', '.join(missing_keys)}"
  )

# クライアントの初期化
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

x_client = tweepy.Client(
    consumer_key=X_API_KEY,
    consumer_secret=X_API_SECRET,
    access_token=X_ACCESS_TOKEN,
    access_token_secret=X_ACCESS_TOKEN_SECRET,
)

# ==========================================
# 2. システムプロンプト（指示文）
# ==========================================
SYSTEM_PROMPT_ANALYST = """
あなたは神戸市に精通しているローカル住民です。
神戸の街の便利なところ（アクセスの良さや綺麗な景色）と、日常の中で感じるリアルな不満や違和感（三宮への一極集中、ゴミの分別や有料化、街の治安・雰囲気、危険な交差点や歩行者の混雑など）を観察しています。
地元の人が「あ、それめっちゃわかる」「確かにそうだよね」と深く共感できる、具体的でリアルな視点を2〜3個挙げてください。
"""

SYSTEM_PROMPT_MARKETER = """
あなたは神戸のローカル情報・日常の不満を発信するXアカウント（@miltukusukoube）の運営者です。
分析結果を基に、フォロワーからの共感やリプライ（コメント）を引き出す人間味のある日本語のXポストを作成してください。

【厳格な制約】
1. 文字数：200文字以内（絶対に超えないこと）。
2. トーン：自然で少しカジュアルな、個人の日々の呟きのようなトーン。ロボット感や堅苦しさは一切排除する。
3. 構成：
   - 1行目：日常のリアルな発見や、ちょっとした不満・違和感から始める強いフック。
   - 本文：自分の率直な感想や、「みんなはどう思う？」というフォロワーへの問いかけで締める。
4. 文末にハッシュタグ（例: #神戸市）を自然に含める。
"""


# ==========================================
# 3. AI生成＆投稿関数
# ==========================================
def generate_kobe_post(topic: str) -> str:
  """Geminiで文章を分析・作成する"""
  print(f"🤖 [Gemini] トピック『{topic}』を分析中...")

  # Agent 1: 分析 (モデル名を gemini-3.8-flash に指定)
  analyst_response = gemini_client.models.generate_content(
      model="gemini-3.8-flash",
      contents=f"{SYSTEM_PROMPT_ANALYST}\n\n以下の神戸のローカルなトピックを分析してください: {topic}",
  )
  analysis_result = analyst_response.text or ""

  # Agent 2: ポスト作成 (モデル名を gemini-3.8-flash に指定)
  marketer_response = gemini_client.models.generate_content(
      model="gemini-3.8-flash",
      contents=(
          f"{SYSTEM_PROMPT_MARKETER}\n\n以下の分析結果を基に、200文字以内の自然な日本語のXポストを作成してください:\n{analysis_result}"
      ),
  )

  return (marketer_response.text or "").strip()


def post_to_x(text: str):
  """X APIを使って実際にツイートを投稿する"""
  try:
    response = x_client.create_tweet(text=text)
    print(f"✅ Xへの自動投稿成功！ (Tweet ID: {response.data['id']})")
  except Exception as e:
    print(f"❌ Xへの投稿エラー: {e}")


# ==========================================
# 4. 実行メイン処理
# ==========================================
if __name__ == "__main__":
  kobe_topics = [
      "三宮への一極集中：買い物も遊びも全部三宮に集まりすぎて、離れたエリアが不便な件",
      "ゴミの有料化と細かすぎる分別ルール：市民の負担が大きすぎるという現実",
      "三宮の夜の独特な雰囲気：路上でのキャッチや酔っ払い、謎のナンパの多さ",
  ]

  print("=== 神戸ローカルX自動投稿システム開始 ===\n")

  for t in kobe_topics:
    # 1. 文章を生成
    post_text = generate_kobe_post(t)
    print(f"--- [生成されたテキスト ({len(post_text)}文字)] ---")
    print(post_text)

    # 2. Xに自動投稿
    post_to_x(post_text)

    print("=" * 40 + "\n")

    # 投稿間隔（10秒待機）
    time.sleep(10)