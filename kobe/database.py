import os
from supabase import create_client, Client
from dotenv import load_dotenv
from typing import Dict, List, Union, Any

# .env ファイルから環境変数を読み込む
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SupabaseのURLまたはAPIキーが環境変数に設定されていません。")

# シングルトンとしてSupabaseクライアントを初期化
_supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

_supabase_admin_client: Client = create_client(
    SUPABASE_URL, 
    SUPABASE_SERVICE_ROLE_KEY if SUPABASE_SERVICE_ROLE_KEY else SUPABASE_KEY
)

def get_supabase_client() -> Client:
    """通常用のSupabaseクライアントを返却する"""
    return _supabase_client

def get_supabase_admin_client() -> Client:
    """管理者権限（service_role）を持つSupabaseクライアントを返却する"""
    return _supabase_admin_client


# --- 各テーブルへのデータ操作（CRUD 関数群） ---

def upsert_chat_history(history_id: str, query: str, results: Union[List[Any], Dict[str, Any]]):
    """
    1. chat_histories テーブルへ検索・分析履歴を管理者権限で保存・更新する
    """
    # 通常クライアントではなく管理者クライアントを使用する
    client = get_supabase_admin_client()
    try:
        response = client.table("chat_histories").upsert({
            "id": history_id,
            "query": query,
            "results": results
        }, on_conflict="id").execute()
        return response
    except Exception as e:
        print(f"History save error: {e}")
        return None

def insert_sns_post(post_data: dict):
    """
    2. sns_posts テーブルへデータを単体で挿入・更新する
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
    4. ai_analysis_results テーブルへAI解析結果を管理者権限で挿入・更新する
    """
    # 通常クライアント(client)の代わりに管理者クライアント(admin_client)を使用
    client = get_supabase_admin_client()
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