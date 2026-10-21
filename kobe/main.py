import os
import resend
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from supabase import Client
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from datetime import datetime, timezone
import uuid
import uvicorn

# 自作モジュール
from ai import *
from database import *
from x import *

from stop_flag import request_stop_ai, request_stop_x

app = FastAPI(
    title="Unmute City Backend",
    description="SNSから市民の不満や課題を収集・AI分析し、ロジックツリーを生成するAPI",
    version="1.0.0"
)

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 各種クライアントの初期化
supabase: Client = get_supabase_client()
x_client = XPostCollector()  
RESEND_FROM = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

class IssueCatalogCreate(BaseModel):
    history_id: Optional[str]
    main_category: str
    sub_category: str
    detail_category: str
    ai_summary: str
    total_priority_score: int

class UserSchema(BaseModel):
    name: str
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
            "options": {"data": {"full_name": user.name}}
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

@app.put("/api/users/password-reset")
def reset_password(data: PasswordResetSchema):
    try:
        users_response = supabase.auth.admin.list_users()
        target_user = next((u for u in users_response if getattr(u, 'email', None) == data.email), None)
        
        if not target_user:
            raise HTTPException(status_code=404, detail="該当するユーザがいません")
        
        response = supabase.auth.admin.update_user_by_id(
            target_user.id,
            {"password": data.new_password}
        )
        return {"message": "パスワードが正常に上書きされました", "data": response}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/forgot-password")
def forgot_password(data: PasswordResetRequestSchema):
    try:
        users_response = supabase.auth.admin.list_users()
        target_user = next((u for u in users_response if getattr(u, 'email', None) == data.email), None)
        if not target_user:
            raise HTTPException(status_code=404, detail="登録されていないメールアドレスです")
        reset_link = "https://koube-production.up.railway.app/reset2.html"
        
        params = {
            "from": RESEND_FROM,
            "to": [data.email],
            "subject": "【神戸市SNS分析ダッシュボード】パスワード再設定のご案内",
            "html": f"""
                <div style="font-family: sans-serif; color: #333; padding: 20px; max-width: 600px; margin: 0 auto;">
                    <h2 style="color: #1a73e8;">パスワード再設定のご案内</h2>
                    <p>神戸市SNS分析ダッシュボードをご利用いただきありがとうございます。</p>
                    <p>パスワード再設定のリクエストを受け付けました。以下のボタンをクリックして設定してください。</p>
                    <p style="margin: 30px 0; text-align: center;">
                        <a href="{reset_link}" style="background-color: #1a73e8; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">
                            パスワードを再設定する
                        </a>
                    </p>
                </div>
            """
        }
        email_response = resend.Emails.send(params)  # type: ignore
        return {"message": "パスワード再設定メールを送信しました", "resend_data": email_response}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# 1. SNSデータ収集 & AI解析トリガーAPI (バックグラウンド実行)
@app.post("/api/collect")
async def trigger_collection(background_tasks: BackgroundTasks):
    """
    x.py を直接呼び出し、CSVマスター（町名・駅名・感情ワード）を使った
    完全自動AND/OR分析・収集パイプラインをバックグラウンドで起動する
    """
    def run_pipeline():
        print("=== 【x.py 連携】全自動AND/ORキーワード分析・収集パイプラインを開始 ===")
        try:
            # x.py で定義されている XPostCollector のインスタンス（x_client）を利用
            if hasattr(x_client, "run_full_scan"):
                # 全件フルスキャン（whileループで最後まで回す）を実行
                x_client.run_full_scan()
            elif hasattr(x_client, "fetch_posts_by_keyword"):
                # フルスキャンが無い場合は1回分のフェッチを実行
                x_client.fetch_posts_by_keyword(max_results=10)
            else:
                print("⚠️ x_client に実行メソッドが見つかりません。")
        except Exception as e:
            print(f"💥 X収集パイプライン実行エラー: {e}")

    background_tasks.add_task(run_pipeline)
    return {"message": "x.py を用いた全自動データ収集・AI分析パイプラインがバックグラウンドで開始されました。"}
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

# 4. 履歴一覧取得API
@app.get("/api/histories")
def get_chat_histories():
    try:
        response = supabase.table("chat_histories").select("id, query, created_at").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 5. 履歴削除API
@app.delete("/api/history/{history_id}")
def delete_chat_history(history_id: str):
    try:
        response = supabase.table("chat_histories").delete().eq("id", history_id).execute()
        return {"message": "履歴を削除しました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 例: 不満・課題（complaint）に絞ったAI分析結果を取得するAPI
@app.get("/api/ai-analyses/complaints")
def get_complaint_analyses():
    try:
        # post_type が 'complaint' のものだけを結合して取得
        response = supabase.table("ai_analysis_results").select(
            "id, is_valid_issue, post_type, sentiment_score, priority_score, analyzed_at, sns_posts(id, platform, post_text, likes_count, collected_at)"
        ).eq("post_type", "complaint").order("priority_score", desc=True).execute()
        
        return {"complaints": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 6. AI分析結果の一覧と紐づくSNS投稿・地域分類を取得するAPI（Web表示用）
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

# --- キーワード検索 & Web表示用API ---
@app.get("/api/search")
def search_issues(q: str = Query(..., description="検索キーワード")):
    try:
        # 修正の例：まずは sns_posts の基本情報と likes_count 等だけで安全に検索する
        response = supabase.table("sns_posts").select(
            "id, platform, post_text, likes_count, collected_at"
        ).ilike("post_text", f"%{q}%").order("collected_at", desc=True).execute()
        
        posts = []
        if response.data and isinstance(response.data, list):
            for item in response.data:
                # Supabaseのオブジェクト形式に合わせてドットアクセスに変更
                posts.append({
                    "id": getattr(item, "id", None) if not isinstance(item, dict) else item.get("id"),
                    "summary": getattr(item, "ai_summary", None) if not isinstance(item, dict) else item.get("ai_summary"),
                    "post_text": getattr(item, "post_text", None) if not isinstance(item, dict) else item.get("post_text"),
                    "matched_area": getattr(item, "matched_area", None) if not isinstance(item, dict) else item.get("matched_area"),
                    "ward": getattr(item, "ward", None) if not isinstance(item, dict) else item.get("ward"),
                    "likes": getattr(item, "likes_count", 0) if not isinstance(item, dict) else item.get("likes_count", 0),
                    "topic_key": getattr(item, "platform", "X") if not isinstance(item, dict) else item.get("platform", "X"),
                    "collected_at": getattr(item, "collected_at", None) if not isinstance(item, dict) else item.get("collected_at"),
                    "sentiment_score": getattr(item, "sentiment_score", 0.0) if not isinstance(item, dict) else item.get("sentiment_score", 0.0),
                    "priority_score": getattr(item, "total_priority_score", 0) if not isinstance(item, dict) else item.get("total_priority_score", 0)
                })
                
        history_id = str(uuid.uuid4())[:16]
        try:
            supabase.table("chat_histories").insert({
                "id": history_id,
                "query": q,
                "results": {"posts": posts}
            }).execute()
        except Exception as insert_err:
            print(f"History save error: {insert_err}")
            
        return {"query": q, "history_id": history_id, "posts": posts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/posts")
def create_post(post: PostCreate):
    try:
        response = supabase.table("sns_posts").insert({
            "platform": post.platform,
            "post_text": post.post_text,
            "original_post_id": f"web_{uuid.uuid4()}"
        }).execute()
        return {"message": "投稿が保存されました", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/posts")
def get_posts():
    try:
        response = supabase.table("sns_posts").select("*").order("id", desc=True).execute()
        return {"posts": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# AI解析の一括実行API
@app.post("/api/run-ai-analysis")
def run_ai_analysis_for_all_posts(background_tasks: BackgroundTasks):
    def task():
        ai_engine = get_ai_engine()
        ai_engine.analyze_posts_from_db()

    background_tasks.add_task(task)
    return {"message": "sns_posts からのAI一括解析処理をバックグラウンドで開始しました。"}

@app.get("/api/history/{history_id}")
def get_history_detail(history_id: str, sentiment: str = "all"):
    try:
        response = supabase.table("chat_histories").select("*").eq("id", history_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="指定された履歴が見つかりません")
        
        raw_data = response.data[0]
        history_data: dict = raw_data if isinstance(raw_data, dict) else {}
        
        results_obj = history_data.get("results")
        posts = results_obj.get("posts") if isinstance(results_obj, dict) else []
        if not isinstance(posts, list):
            posts = []
        
        if sentiment == "positive":
            filtered_posts = [p for p in posts if isinstance(p, dict) and float(p.get("sentiment_score", 0) or 0) > 0]
        elif sentiment == "negative":
            filtered_posts = [p for p in posts if isinstance(p, dict) and float(p.get("sentiment_score", 0) or 0) < 0]
        elif sentiment == "neutral":
            filtered_posts = [p for p in posts if isinstance(p, dict) and float(p.get("sentiment_score", 0) or 0) == 0]
        else:
            filtered_posts = posts
            
        return {
            "query": history_data.get("query", ""),
            "results": {"posts": filtered_posts},
            "summary": history_data.get("summary")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/stop-x")
def stop_x_collection():
    request_stop_x()
    return {"message": "Xデータ収集の中断リクエストを送信しました。"}

@app.post("/api/stop-ai")
def stop_ai_analysis():
    request_stop_ai()
    return {"message": "AI一括解析の中断リクエストを送信しました。"}

@app.get("/api/keywords")
def get_keywords():
    try:
        kobe_keywords = [
            "神戸", "神戸市", "東灘区", "灘区", "中央区", "兵庫区", "長田区", 
            "須磨区", "垂水区", "北区", "西区", "三宮", "三ノ宮", "元町", "新長田"
        ]
        complaint_keywords = [
            "不満", "改善", "要望", "問題", "危険", "道路", "インフラ", 
            "渋滞", "混雑", "バス", "電車", "公園", "ゴミ", "子育て", "行政"
        ]
        return {
            "kobe_keywords": kobe_keywords,
            "complaint_keywords": complaint_keywords
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 地域別・行政区別の集計統計API ---
@app.get("/api/stats/regions")
def get_region_stats():
    try:
        response = supabase.table("sns_posts").select("*").execute()
        ward_counts = {}
        town_counts = {}  
        
        if response.data and isinstance(response.data, list):
            ai_engine = get_ai_engine()
            for p in response.data:
                if isinstance(p, dict):
                    text = str(p.get("post_text") or "")
                    if not text.strip():
                        continue
                    
                    res = ai_engine.regional_classifier.classify_text(text)
                    
                    if isinstance(res, dict):
                        ward = res.get("ward") or "その他・分類不能"
                        area = res.get("matched_area") or "その他・主要駅"
                    else:
                        ward = "その他・分類不能"
                        area = "その他・主要`駅"

                    if str(ward) == "None" or not ward:
                        ward = "その他・分類不能"
                    if str(area) == "None" or not area:
                        area = "その他・主要駅"

                    ward_counts[ward] = ward_counts.get(ward, 0) + 1
                    town_counts[area] = town_counts.get(area, 0) + 1
                        
        sorted_wards = dict(sorted(ward_counts.items(), key=lambda x: x[1], reverse=True))
        sorted_towns = dict(sorted(town_counts.items(), key=lambda x: x[1], reverse=True))
        
        return {
            "ward_counts": sorted_wards,
            "town_counts": sorted_towns
        }
    except Exception as e:
        print(f"Region stats error: {e}")
        return {"ward_counts": {}, "town_counts": {}}
    
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)