import os
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 自作モジュール（プロジェクト内に配置する想定）
from x import XApiClient
from ai import AnalysisEngine
from database import get_supabase_client

app = FastAPI(
    title="Unmute City Backend",
    description="SNSから市民の不満や課題を収集・AI分析し、ロジックツリーを生成するAPI",
    version="1.0.0"
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

# リクエストデータの形を定義
class PostCreate(BaseModel):
    post_text: str
    platform: str = "WebDashboard"

# --- 静的ファイル・フロントエンド配信設定 ---

os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# 💡 ルート (http://192.168.6.36:8000/) にアクセスしたときに login.html を返すように変更
@app.get("/", include_in_schema=False)
def serve_root():
    path = "static/login.html"
    if not os.path.exists(path):
        return {"error": "login.html not found in static folder."}
    return FileResponse(path)


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
    try:
        response = supabase.auth.sign_in_with_password({
            "email": user.email,
            "password": user.password
        })
        return {"message": "ログイン成功", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) 

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


# --- 個別HTMLファイル配信設定 ---

@app.get("/login.html", include_in_schema=False)
def serve_login():
    path = "static/login.html"
    if not os.path.exists(path):
        return {"error": "login.html not found in static folder."}
    return FileResponse(path)

@app.get("/login2.html", include_in_schema=False)
def serve_login2():
    path = "static/login2.html"
    if not os.path.exists(path):
        return {"error": "login2.html not found in static folder."}
    return FileResponse(path)

# ログイン後のメインダッシュボード画面へのルーティング
# ログイン後のメインダッシュボード画面へのルーティング
@app.get("/app", include_in_schema=False)
def serve_app_dashboard():
    path = "static/mian.html"
    if not os.path.exists(path):
        return {"error": "mian.html not found in static folder."}
    return FileResponse(path)

# --- 検索・D3.jsツリー用 API ---

@app.get("/api/search")
def search_issues(q: str = Query(..., description="検索キーワード")):
    """
    フロントエンドからの検索キーワードを受け取り、対応する課題データを返す
    """
    try:
        response = supabase.table("issue_catalog").select("*").ilike("ai_summary", f"%{q}%").execute()
        
        posts = []
        if response.data:
            for item in response.data:
                if isinstance(item, dict):
                    posts.append({
                        "title": item.get("ai_summary", "無題の課題"),
                        "likes": item.get("total_priority_score", 0),
                        "topic_key": item.get("sub_category", q)
                    })
            
        if not posts:
            posts = [
                {"title": f"「{q}」に関する市民の意見・不満データ1", "likes": 42, "topic_key": q},
                {"title": f"「{q}」に関するインフラの課題", "likes": 18, "topic_key": q}
            ]

        return {"query": q, "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tree/{topic_key}/{tree_type}")
def get_d3_tree_data(topic_key: str, tree_type: str):
    """
    D3.jsのツリー描画用に、What / Why / How 別の階層構造JSONを返す
    """
    try:
        tree_data = {
            "name": f"{topic_key} [{tree_type}]",
            "children": [
                {
                    "name": "主要因 1",
                    "children": [
                        {"name": "詳細データ A"},
                        {"name": "詳細データ B"}
                    ]
                },
                {
                    "name": "主要因 2",
                    "children": [
                        {"name": "詳細データ C"}
                    ]
                }
            ]
        }
        return tree_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/posts")
def create_post(post: PostCreate):
    """
    ウェブ画面から新しい投稿を保存するAPI
    """
    try:
        response = supabase.table("sns_posts").insert({
            "platform": post.platform,
            "post_text": post.post_text,
            "posted_at": "now()"
        }).execute()
        return {"message": "投稿が保存されました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/posts")
def get_posts():
    try:
        response = supabase.table("sns_posts").select("*").order("created_at",desc=True).execute()
        return {"posts":response.data}
    except Exception as e:
        raise HTTPException(status_code=500,detail=str(e))
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)