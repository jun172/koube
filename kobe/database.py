import os
from supabase import create_client, Client
from dotenv import load_dotenv
from typing import Dict, List, Union, Any
from google.cloud import language_v1  # Natural Language API用
from google import genai              # Gemini API用（新しい Google Gen AI SDK）

# .env ファイルから環境変数を読み込む
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # または GOOGLE_API_KEY

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SupabaseのURLまたはAPIキーが環境変数に設定されていません。")

# シングルトンとしてSupabaseクライアントを初期化
_supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_supabase_client() -> Client:
    """
    Supabaseクライアントインスタンスを返却する
    """
    return _supabase_client


# --- Google Cloud Natural Language API による解析関数 ---

def analyze_text_with_natural_language(text: str) -> Dict[str, Any]:
    """
    Google Cloud Natural Language API を使用して、
    テキストの感情分析（スコア）とエンティティ（重要キーワード）抽出を行う
    """
    client = language_v1.LanguageServiceClient()

    document = language_v1.Document(
        content=text,
        type_=language_v1.Document.Type.PLAIN_TEXT,
        language="ja",  # 日本語を指定
    )

    # 1. 感情分析の実行
    sentiment_response = client.analyze_sentiment(request={"document": document})
    sentiment = sentiment_response.document_sentiment

    # 2. エンティティ分析（キーワード抽出）の実行
    entities_response = client.analyze_entities(request={"document": document})
    
    extracted_entities = []
    for entity in entities_response.entities:
        extracted_entities.append({
            "name": entity.name,
            "type": language_v1.Entity.Type(entity.type_).name,
            "salience": entity.salience, # 重要度 (0.0 ~ 1.0)
        })

    return {
        "score": sentiment.score,          # -1.0 (ネガティブ) 〜 1.0 (ポジティブ)
        "magnitude": sentiment.magnitude,  # 感情の強さ
        "entities": extracted_entities     # 抽出されたキーワード一覧
    }


# --- Gemini API による要約・タイトル生成関数 ---

def generate_summary_and_title_with_gemini(text: str) -> Dict[str, str]:
    """
    Gemini API を使用して、投稿文から「タイトル」と「要約」を生成する
    """
    # クライアントの初期化（環境変数の GOOGLE_API_KEY または GEMINI_API_KEY を自動読込）
    client = genai.Client()

    prompt = f"""
以下のSNS投稿・フィードバック文から、内容を端的に表す「タイトル」と、2〜3文程度の「要約」を作成してください。
出力は必ず以下のJSON形式（マークダウンのコードブロックなし）で返してください。
{{
  "title": "ここにタイトル",
  "summary": "ここに要約文"
}}

対象のテキスト：
{text}
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",  # 用途に応じたモデルを指定
            contents=prompt,
        )
        
        import json
        # レスポンスからJSON部分をパース（必要に応じてクリーニング）
        # レスポンスからJSON部分をパース（None対策を追加）
        
        raw_text = response.text if response.text else "{}"
        cleaned_text = raw_text.strip().replace("```json", "").replace("```", "").strip()
        result_json = json.loads(cleaned_text)
        
        return {
            "title": result_json.get("title", "無題の投稿"),
            "summary": result_json.get("summary", text[:100] + "...")
        }
    except Exception as e:
        print(f"Error generating summary with Gemini: {e}")
        return {
            "title": "要約生成エラー",
            "summary": text[:100] + "..."
        }


# --- 各テーブルへのデータ操作（CRUD + AI解析・Gemini連携） ---

def upsert_chat_history(history_id: str, query: str, results: Union[List[Any], Dict[str, Any]]):
    """
    1. chat_histories テーブルへ検索・分析履歴を保存・更新する
    """
    client = get_supabase_client()
    try:
        response = client.table("chat_histories").upsert({
            "id": history_id,
            "query": query,
            "results": results
        }, on_conflict="id").execute()
        return response
    except Exception as e:
        print(f"Error upserting chat_history: {e}")
        return None

def insert_sns_post_with_ai_analysis(post_data: dict, text_field_name: str = "content"):
    """
    2. sns_posts テーブルへの保存と同時に、Gemini（要約・タイトル）と Natural Language（感情・キーワード）を両方実行して保存する例
    """
    client = get_supabase_client()
    try:
        post_text = post_data.get(text_field_name)
        
        # 1. Geminiでタイトルと要約を生成して post_data に追加
        if post_text:
            gemini_result = generate_summary_and_title_with_gemini(post_text)
            post_data["title"] = gemini_result["title"]
            post_data["summary"] = gemini_result["summary"]

        # 2. SNS投稿データを保存（タイトルや要約カラムがある場合も一緒に保存）
        response = client.table("sns_posts").upsert(
            post_data, 
            on_conflict="original_post_id"
        ).execute()

        # 3. Natural Language APIで感情・キーワード解析を実行
        if post_text:
            nl_analysis = analyze_text_with_natural_language(post_text)
            
            analysis_payload = {
                "sns_post_id": post_data.get("id"),
                "sentiment_score": nl_analysis["score"],
                "sentiment_magnitude": nl_analysis["magnitude"],
                "entities": nl_analysis["entities"]
            }
            client.table("ai_analysis_results").upsert(
                analysis_payload,
                on_conflict="sns_post_id"
            ).execute()

        return response
    except Exception as e:
        print(f"Error inserting sns_post with AI analysis: {e}")
        return None

def insert_sns_post(post_data: dict):
    """
    sns_posts テーブルへデータを単体で挿入・更新する
    """
    client = get_supabase_client()
    try:
        response = client.table("sns_posts").upsert(
            post_data, 
            on_conflict="original_post_id"
        ).execute()
        return response
    except Exception as e:
        print(f"Error inserting sns_post: {e}")
        return None

def upsert_issue_catalog(catalog_data: dict):
    """
    3. issue_catalog テーブルへロジックツリーの分類データを登録・更新する
    """
    client = get_supabase_client()
    try:
        response = client.table("issue_catalog").upsert(
            catalog_data,
            on_conflict="id"
        ).execute()
        return response
    except Exception as e:
        print(f"Error upserting issue_catalog: {e}")
        return None

def insert_ai_analysis(analysis_data: dict):
    """
    4. ai_analysis_results テーブルへAI解析結果を挿入・更新する
    """
    client = get_supabase_client()
    try:
        response = client.table("ai_analysis_results").upsert(
            analysis_data,
            on_conflict="sns_post_id"
        ).execute()
        return response
    except Exception as e:
        print(f"Error inserting ai_analysis_results: {e}")
        return None

def link_issue_and_post(issue_id: int, sns_post_id: int):
    """
    5. issue_post_relations 中間テーブルに課題と投稿の紐付けを保存する
    """
    client = get_supabase_client()
    try:
        response = client.table("issue_post_relations").upsert({
            "issue_id": issue_id,
            "sns_post_id": sns_post_id
        }, on_conflict="issue_id,sns_post_id").execute()
        return response
    except Exception as g:
        print(f"Error linking issue and post: {g}")
        return None

def insert_quiz_question(question_data: dict):
    """
    6. quiz_questions テーブルへクイズ問題データを挿入する
    """
    client = get_supabase_client()
    try:
        response = client.table("quiz_questions").insert(question_data).execute()
        return response
    except Exception as e:
        print(f"Error inserting quiz_question: {e}")
        return None

def insert_quiz_response(response_data: dict):
    """
    7. quiz_responses テーブルへユーザーの回答履歴を挿入する
    """
    client = get_supabase_client()
    try:
        response = client.table("quiz_responses").insert(response_data).execute()
        return response
    except Exception as e:
        print(f"Error inserting quiz_response: {e}")
        return None
    
def insert_threads_comment(comment_data: dict):
    """
    8. Threadsのコメントデータを threads_comments テーブルへ挿入・更新する
    """
    client = get_supabase_client()
    try:
        response = client.table("threads_comments").upsert(
            comment_data, 
            on_conflict="comment_id"
        ).execute()
        return response
    except Exception as e:
        print(f"Error inserting threads_comment: {e}")
        return None