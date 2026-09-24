import os
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from supabase import Client
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from datetime import datetime, timezone
import uuid


# 自作モジュール
from ai import *
from database import *
from x import *


app = FastAPI(
    title="Unmute City Backend",
    description="SNSから市民の不満や課題を収集・AI分析し、ロジックツリーを生成するAPI",
    version="1.0.0"
)

# CORS設定（ブラウザからのアクセスエラーを防ぐため特定のオリジンまたは柔軟に設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://0.0.0.0/8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 各種クライアントの初期化
supabase: Client = get_supabase_client()
x_client = XPostClient()  

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
    name:str
    email: str
    password: str

class SignupSchema(BaseModel):
    name: str
    email: str
    password: str

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

# 1. ルート（ログイン画面など）
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
            "password": user.password,
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
        response = supabase.auth.reset_password_for_email(
            data.email,
            options={
                "redirect_to": "http://localhost:8000/reset2.html"
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
            
            for post in posts:
                try:
                    saved_res = supabase.table("sns_posts").upsert(
                        post, 
                        on_conflict="original_post_id"
                    ).select().execute() # .select() を付加して戻り値（id）を確実に取得

                    if saved_res and hasattr(saved_res, "data") and isinstance(saved_res.data, list) and len(saved_res.data) > 0:
                        first_row = saved_res.data[0]
                        if isinstance(first_row, dict):
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


# 4. 履歴一覧・詳細取得API
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

# 5. 削除
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


# --- キワードから投稿を取得 API ---
@app.get("/api/search")
def search_issues(q: str = Query(..., description="検索キーワード")):
    try:
        # 1. キーワードにヒットする sns_posts を取得
        response = supabase.table("sns_posts").select(
            "id, platform, post_text, likes_count, collected_at"
        ).ilike("post_text", f"%{q}%").execute()
        
        posts = []
        if response.data:
            for post_info in response.data:
                if isinstance(post_info, dict):
                    post_id = post_info.get("id")
                    text = str(post_info.get("post_text") or "")
                    
                    # 2. ローカルの分析エンジンで要約と感情スコアを算出（Gemini API不使用）
                    summary_text = text[:40]
                    sentiment_score = 0.0
                    
                    try:
                        # ai.pyなどに定義したローカルの軽量分析関数を呼び出し
                        local_result = analyze_sentiment_and_summary(text)
                        summary_text = local_result.get("summary", summary_text)
                        sentiment_score = float(local_result.get("sentiment_score", 0.0))
                    except Exception as local_err:
                        print(f"Local NLP analysis error: {local_err}")

                    # --- ★ 極端な感情表現の除外フィルター ---
                    THRESHOLD = 0.7
                    if abs(sentiment_score) > THRESHOLD:
                        # スコアが極端な場合はリストに追加せずスキップ
                        continue
                    # ----------------------------------------

                    posts.append({
                        "id": post_id,
                        "summary": summary_text,
                        "likes": post_info.get("likes_count", 0),
                        "topic_key": post_info.get("platform", "X"),
                        "collected_at": post_info.get("collected_at"),
                        "sentiment_score": sentiment_score 
                    })
                
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
    

# ロジックツリーデータ取得API
@app.get("/api/tree/{post_id}/{tree_type}")
def get_d3_tree_data(post_id: str, tree_type: str):
    try:
        # 1. いいね数を取得（投稿文は取得・使用しない）
        likes = 0
        post_res = supabase.table("sns_posts").select("likes_count").eq("id", post_id).execute()
        if post_res.data and isinstance(post_res.data, list) and len(post_res.data) > 0:
            first_post = post_res.data[0]
            if isinstance(first_post, dict):
                try:
                    raw_likes = first_post.get("likes_count", 0)
                    likes = int(str(raw_likes)) 
                except (TypeError, ValueError):
                    likes = 0

        # 2. 課題カタログ（issue_catalog）または分析結果を取得
        relation_res = supabase.table("issue_post_relations").select("issue_id").eq("sns_post_id", post_id).execute()
        
        main_c = "一般課題"
        sub_c = "詳細分類"
        detail_c = "個別要件"
        summary = "要約なし"
        sentiment = 0.0
        priority = 50

        if relation_res.data and isinstance(relation_res.data, list) and len(relation_res.data) > 0:
            first_rel = relation_res.data[0]
            if isinstance(first_rel, dict):
                issue_id = first_rel.get("issue_id")
                if issue_id is not None:
                    catalog_res = supabase.table("issue_catalog").select("*").eq("id", issue_id).execute()
                    if catalog_res.data and isinstance(catalog_res.data, list) and len(catalog_res.data) > 0:
                        cat = catalog_res.data[0]
                        if isinstance(cat, dict):
                            main_c = str(cat.get("main_category", "一般課題"))
                            sub_c = str(cat.get("sub_category", "詳細分類"))
                            detail_c = str(cat.get("detail_category", "個別要件"))
                            summary = str(cat.get("ai_summary", "要約なし"))

        # 分析結果からスコア等を安全に取得
        analysis_res = supabase.table("ai_analysis_results").select("*").eq("sns_post_id", post_id).execute()
        if analysis_res.data and isinstance(analysis_res.data, list) and len(analysis_res.data) > 0:
            row = analysis_res.data[0]
            if isinstance(row, dict):
                raw_sent = row.get("sentiment_score", 0.0)
                try:
                    sentiment = float(raw_sent) # type: ignore
                except (TypeError, ValueError):
                    sentiment = 0.0

                raw_prio = row.get("priority_score", 50)
                try:
                    priority = int(raw_prio) # type: ignore
                except (TypeError, ValueError):
                    priority = 50

        # 3. 投稿文をなくし、メインカテゴリー・AI要約・評価の枝だけに整理した構造
        children = [
            {
                "name": main_c,
                "children": [
                    {"name": detail_c}
                ]
            },
            {
                "name": "AI要約",
                "children": [
                    {"name": summary}
                ]
            },
            {
                "name": "評価",
                "children": [
                    {"name": f"深刻度: {priority} / いいね: {likes}"}
                ]
            }
        ]
        
        return {
            "name": "要素分類",  # ルートのラベルをスッキリとした名前に変更
            "children": children
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/posts")
def create_post(post: PostCreate):
    try:
        # ai_analysis_results ではなく sns_posts テーブルへインサートする
        response = supabase.table("sns_posts").insert({
            "platform": post.platform,
            "post_text": post.post_text,
            "original_post_id": f"web_{uuid.uuid4()}" # 重複回避用のダミーIDなどを付与
        }).execute()
        return {"message": "投稿が保存されました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/posts")
def get_posts():
    try:
        # sns_posts テーブルから全件取得する
        response = supabase.table("sns_posts").select("*").order("id", desc=True).execute()
        return {"posts": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tree/post/{post_id}")
def get_post_tree_data(post_id: int):
    try:
        # 1. 投稿IDに紐づく issue_catalog のデータを取得（中間テーブル経由）
        relation_res = supabase.table("issue_post_relations").select("issue_id").eq("sns_post_id", post_id).execute()
        
        if not relation_res or not relation_res.data:
            raise HTTPException(status_code=404, detail="この投稿に対するツリーデータが見つかりません")
        
        relation_item = relation_res.data[0]
        
        # 安全な型チェックと値の取得
        if isinstance(relation_item, dict):
            issue_id = relation_item.get("issue_id")
        else:
            issue_id = getattr(relation_item, "issue_id", None)
            
        if not issue_id:
            raise HTTPException(status_code=404, detail="課題IDの取得に失敗しました")

        issue_res = supabase.table("issue_catalog").select("*").eq("id", issue_id).execute()
        
        if not issue_res or not issue_res.data:
            raise HTTPException(status_code=404, detail="課題カタログが見つかりません")
            
        issue = issue_res.data[0]
        if not issue:
            raise HTTPException(status_code=404, detail="課題データが空です")

        # 2. データベースのカテゴリ階層（大・中・小）からD3.js用のツリー構造を組み立てる
        if isinstance(issue, dict):
            main_cat = issue.get("main_category") or "一般課題"
            sub_cat = issue.get("sub_category") or "その他"
            detail_cat = issue.get("detail_category") or issue.get("ai_summary") or "詳細なし"
        else:
            main_cat = getattr(issue, "main_category", None) or "一般課題"
            sub_cat = getattr(issue, "sub_category", None) or "その他"
            detail_cat = getattr(issue, "detail_category", None) or getattr(issue, "ai_summary", None) or "詳細なし"

        tree_data = {
            "name": main_cat,
            "children": [
                {
                    "name": sub_cat,
                    "children": [
                        {"name": detail_cat}
                    ]
                }
            ]
        }

        return tree_data
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# --- 追加：sns_posts のデータから AI解析を再実行・一括生成するAPI ---
@app.post("/api/run-ai-analysis")
def run_ai_analysis_for_all_posts(background_tasks: BackgroundTasks):
    """
    sns_posts テーブルにある全データに対してAI解析を再度バックグラウンドで実行し、
    ai_analysis_results や issue_catalog へデータを登録・更新する
    """
    def task():
        ai_engine = get_ai_engine()
        # ai.py 内にある既存のメソッドを呼び出して全件解析を実行
        ai_engine.analyze_posts_from_db()

    background_tasks.add_task(task)
    return {"message": "sns_posts からのAI一括解析処理をバックグラウンドで開始しました。"}

@app.get("/api/history/{history_id}")
def get_history_detail(history_id: str, sentiment: str = "all"):
    try:
        response = supabase.table("chat_histories").select("*").eq("id", history_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="指定された履歴が見つかりません")
        
        # 確実に辞書型として扱うためのキャスト
        raw_data = response.data[0]
        history_data: dict = raw_data if isinstance(raw_data, dict) else {}
        
        # results や posts を安全に取得
        results_obj = history_data.get("results")
        if not results_obj or not isinstance(results_obj, dict):
            results_obj = {}
            
        posts = results_obj.get("posts")
        if not posts or not isinstance(posts, list):
            posts = []
        
        # 感情に応じたフィルタリング
        if sentiment == "positive":
            filtered_posts = [
                p for p in posts 
                if isinstance(p, dict) and float(p.get("sentiment_score", 0) or 0) > 0
            ]
        elif sentiment == "negative":
            filtered_posts = [
                p for p in posts 
                if isinstance(p, dict) and float(p.get("sentiment_score", 0) or 0) < 0
            ]
        else:
            filtered_posts = posts
            
        query_val = history_data.get("query", "")
        summary_val = history_data.get("summary")

        return {
            "query": query_val,
            "results": {
                "posts": filtered_posts
            },
            "summary": summary_val
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)