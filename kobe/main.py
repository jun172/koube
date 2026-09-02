import os
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client

# 自作モジュール（プロジェクト内に配置する想定）
from x import XApiClient
from ai import AnalysisEngine
from database import get_supabase_client

app = FastAPI(
    title="Unmute City Backend",
    description="SNSから市民の不満や課題を収集・AI分析し、ロジックツリーを生成するAPI",
    version="1.0.0"
)

# CORS設定（Reactフロントエンドからのアクセスを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番環境ではフロントエンドのURLに制限してください
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 各種クライアントの初期化
x_client = XApiClient()
ai_engine = AnalysisEngine()
supabase: Client = get_supabase_client()


# --- Pydanticスキーマ定義 ---
class CollectionRequest(BaseModel):
    keywords: List[str]
    history_id: Optional[str] = None  # Gemini風の履歴ID（紐づけて保存する場合）

class IssueCatalogCreate(BaseModel):
    history_id: Optional[str]
    main_category: str
    sub_category: str
    detail_category: str
    ai_summary: str
    total_priority_score: int

class UserSchema(BaseModel):
    email: str
    password: str


# --- ルーティング定義 ---

@app.get("/")
def read_root():
    return {"status": "Unmute City Backend is running successfully."}


# --- 認証系 API ---

@app.post("/api/signup")
def signup(user: UserSchema):
    """
    新規ユーザー登録を行うAPI
    """
    try:
        response = supabase.auth.sign_up({
            "email": user.email,
            "password": user.password
        })
        return {"message": "登録成功", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/login")
def login(user: UserSchema):
    """
    ログイン認証を行うAPI
    """
    try:
        response = supabase.auth.sign_in_with_password({
            "email": user.email,
            "password": user.password
        })
        return {"message": "ログイン成功", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- SNSデータ・分析系 API ---

# 1. SNSデータ収集 & AI解析トリガーAPI (バックグラウンド実行)
@app.post("/api/collect")
async def trigger_collection(request: CollectionRequest, background_tasks: BackgroundTasks):
    """
    指定されたキーワードでX（Twitter）からデータを収集し、
    データベースへ保存した後にAI解析パイプラインをバックグラウンドで実行する。
    """
    if not request.keywords:
        raise HTTPException(status_code=400, detail="Keywords must not be empty.")

    def run_pipeline():
        for keyword in request.keywords:
            print(f"Starting collection for keyword: {keyword}")
            posts = x_client.fetch_posts_by_keyword(keyword)
            if not posts:
                continue
            x_client.save_posts_to_db(posts)

            for post in posts:
                try:
                    ai_engine.generate_analysis(
                        post_id=post['original_post_id'], 
                        text=post['post_text'], 
                        like_count=post['likes_count']
                    )
                except Exception as e:
                    print(f"AI Analysis Error for post {post['original_post_id']}: {e}")

    background_tasks.add_task(run_pipeline)
    return {"message": "Data collection and AI analysis pipeline started in background."}


# 2. 課題・ロジックツリーカタログ取得API（Reactフロントエンド用）
@app.get("/api/issues")
def get_issues(history_id: Optional[str] = Query(None, description="Gemini風の履歴IDで絞り込む場合")):
    """
    issue_catalog に蓄積された課題データを取得する。
    """
    try:
        query = supabase.table("issue_catalog").select("*")
        if history_id:
            query = query.eq("history_id", history_id)
        
        response = query.execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 3. 感情分析・優先度スコア結果取得API
@app.get("/api/analysis/{post_id}")
def get_analysis_result(post_id: int):
    """
    指定したポストIDに紐づくAI解析結果を取得する。
    """
    try:
        response = supabase.table("ai_analysis_results").select("*").eq("sns_post_id", post_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Analysis result not found for this post.")
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 4. Gemini風の履歴一覧・詳細取得API
@app.get("/api/histories")
def get_chat_histories():
    """
    サイドバーに表示するための検索・分析履歴の一覧を取得する。
    """
    try:
        response = supabase.table("chat_histories").select("id, query, created_at").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/{history_id}")
def get_chat_history_detail(history_id: str):
    """
    特定の履歴データ（What/Why/HowのツリーJSONを含む）を取得する。
    """
    try:
        response = supabase.table("chat_histories").select("*").eq("id", history_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Chat history not found.")
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)