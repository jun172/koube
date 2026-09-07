import os
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse,HTMLResponse
from datetime import datetime, timezone
import uuid


# 自作モジュール
from x import XApiClient
from ai import get_ai_engine
from database import get_supabase_client,insert_sns_post, upsert_chat_history

app = FastAPI(
    title="Unmute City Backend",
    description="SNSから市民の不満や課題を収集・AI分析し、ロジックツリーを生成するAPI",
    version="1.0.0"
)

# CORS設定（ブラウザからのアクセスエラーを防ぐため特定のオリジンまたは柔軟に設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000","http://0.0.0.0/8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 各種クライアントの初期化
x_client = XApiClient()
supabase: Client = get_supabase_client()


# --- Pydanticスキーマ定義 ---
class CollectionRequest(BaseModel):
    keywords: List[str]
    history_id: Optional[str] = None

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
class SignupSchema(BaseModel):
    email: str
    password: str
    name: str
class PostCreate(BaseModel):
    post_text: str
    platform: str = "WebDashboard"

class PasswordResetSchema(BaseModel):
    email: str
    new_password: str

class PasswordResetRequestSchema(BaseModel):
    email: str
    
# --- 静的ファイル・フロントエンド配信設定 ---

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
def serve_root():
    path = "static/login.html"
    if not os.path.exists(path):
        return {"error": "login.html not found in static folder."}
    return FileResponse(path)


# --- 認証系 API ---

@app.post("/api/signup")
def signup(user: SignupSchema):
    try:
        response = supabase.auth.sign_up({
            "email": user.email,
            "password": user.password,
            "options": {
                "data": {
                    "full_name": user.name
                }
            }
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

# パスワードを忘れた場合の上書きAPI
@app.put("/api/users/password-reset")
def reset_password(data: PasswordResetSchema):
    try:
        users_response = supabase.auth.admin.list_users()
        target_user = next((u for u in users_response if u.email == data.email), None)
        
        if not target_user:
            raise HTTPException(status_code=404, detail="該当するユーザーが見つかりません")
            
        response = supabase.auth.admin.update_user_by_id(
            target_user.id,
            {"password": data.new_password}
        )
        return {"message": "パスワードが正常に上書きされました", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# パスワードリセットメールの送信（Supabase自動処理）
@app.post("/api/forgot-password")
def forgot_password(data: PasswordResetRequestSchema):
    try:
        # Supabaseの機能で指定メールアドレスへパスワード再設定メールを自動送信
        response = supabase.auth.reset_password_for_email(
            data.email,
            options={
                "redirect_to": "http://localhost:8000/reset2.html" # 再設定後に誘導するページ
            }
        )
        return {"message": "パスワード再設定メールを送信しました", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# 1. SNSデータ収集 & AI解析トリガーAPI (バックグラウンド実行)
@app.post("/api/collect")
async def trigger_collection(request: CollectionRequest, background_tasks: BackgroundTasks):
    if not request.keywords:
        raise HTTPException(status_code=400, detail="Keywords must not be empty.")

    def run_pipeline():
        ai_engine = get_ai_engine()
        for keyword in request.keywords:
            print(f"Starting collection for keyword: {keyword}")
            posts = x_client.fetch_posts_by_keyword(keyword)
            if not posts:
                continue
            
            # 各ポストをDBに保存し、発行された整数型の id を取得してAI解析に渡す
            for post in posts:
                try:
                    saved_res = supabase.table("sns_posts").upsert(
                        post, 
                        on_conflict="original_post_id"
                    ).execute()

                    # data がリストであり、かつ最初の要素が辞書型であることを安全に判定・キャストする
                    if saved_res and hasattr(saved_res, "data") and isinstance(saved_res.data, list) and len(saved_res.data) > 0:
                        first_row = saved_res.data[0]
                        if isinstance(first_row, dict):
                            # 明示的に int 型にキャストして Pylance の警告を回避
                            raw_id = first_row.get("id", 0)
                            post_db_id = int(str(raw_id)) if raw_id is not None else 0
                            if post_db_id:
                                ai_engine.generate_analysis(
                                    post_id=post_db_id, 
                                    text=post['post_text'], 
                                    like_count=post['likes_count']
                                )
                except Exception as e:
                    print(f"AI Analysis Pipeline Error: {e}")

    background_tasks.add_task(run_pipeline)
    return {"message": "Data collection and AI analysis pipeline started in background."}


# 2. 課題・ロジックツリーカタログ取得API
@app.get("/api/issues")
def get_issues(history_id: Optional[str] = Query(None)):
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
    try:
        response = supabase.table("chat_histories").select("id, query, created_at").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history/{history_id}")
def get_chat_history_detail(history_id: str):
    try:
        response = supabase.table("chat_histories").select("*").eq("id", history_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Chat history not found.")
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#5.削除
@app.delete("/api/history/{history_id}")
def delete_chat_history(history_id: str):
    try:
        response = supabase.table("chat_histories").delete().eq("id", history_id).execute()
        return {"message": "履歴を削除しました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 6. AI分析結果の一覧と、それに紐づくSNS投稿を取得するAPI
@app.get("/api/ai-analyses")
def get_ai_analyses_with_posts():
    try:
        # ai_analysis_results を取得しつつ、外部キー経由で sns_posts の情報も結合して取得する
        response = supabase.table("ai_analysis_results").select(
            "id, is_valid_issue, sentiment_score, priority_score, analyzed_at, sns_posts(id, platform, post_text, likes_count, collected_at)"
        ).order("analyzed_at", desc=True).execute()
        
        return {"analyses": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 個別HTMLファイル配信設定 ---

@app.get("/login.html", include_in_schema=False)
def serve_login():
    return FileResponse("static/login.html")

@app.get("/reset.html", include_in_schema=False)
def serve_reset():
    return FileResponse("static/reset.html")


@app.get("/app", include_in_schema=False)
def serve_app_dashboard():
    return FileResponse("static/mian.html")

@app.get("/app/{history_id}", include_in_schema=False)
def serve_app_history_dashboard(history_id: str):
    return FileResponse("static/mian.html")

@app.get("/reset2.html", include_in_schema=False)
def serve_reset2():
    return FileResponse("static/reset2.html")

# --- 検索・D3.jsツリー用 API ---
@app.get("/api/search")
def search_issues(q: str = Query(..., description="検索キーワード")):
    try:
        # ai_analysis_results と sns_posts を結合し、AI分析済みで有効な投稿のみを対象にする
        # または issue_catalog からキーワード検索を行う形に切り替える
        response = supabase.table("ai_analysis_results").select(
            "id, sentiment_score, priority_score, sns_posts!inner(id, platform, post_text, likes_count)"
        ).eq("is_valid_issue", True).execute()
        
        posts = []
        if response.data:
            for item in response.data:
                if isinstance(item, dict):
                    post_info = item.get("sns_posts")
                    # post_info が辞書型の場合のみ処理を進める
                    if isinstance(post_info, dict):
                        text = str(post_info.get("post_text") or "")
                        # キーワードが本文に含まれているかフィルター
                        if q.lower() in text.lower():
                            posts.append({
                                "title": text,
                                "likes": post_info.get("likes_count", 0),
                                "topic_key": post_info.get("platform", "X")
                            })

        # 検索履歴の保存
        history_id = str(uuid.uuid4())[:16]
        try:
            supabase.table("chat_histories").insert({
                "id": history_id,
                "query": q,
                "results": {"posts": posts}
            }).execute()
        except Exception as insert_err:
            print(f"History save error (non-fatal): {insert_err}")
            
        return {"query": q, "history_id": history_id, "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/tree/{topic_key}/{tree_type}")
def get_d3_tree_data(topic_key: str, tree_type: str):
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
    try:
        response = supabase.table("ai_analysis_results").insert({
            "platform": post.platform,
            "post_text": post.post_text,
            "collected_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        return {"message": "投稿が保存されました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/posts")
def get_posts():
    try:
        # スキーマ上の実際のタイムスタンプ列に合わせて collected_at で降順ソート
        response = supabase.table("ai_analysis_results").select("*").order("sns_post_id", desc=True).execute()
        return {"posts": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)