import os
from typing import Dict, Any
from dotenv import load_dotenv
from google.cloud import language_v1
import numpy as np
from agents import Agent, Runner, function_tool

# database.py から Supabase 接続および保存関数をインポート
from database import *

load_dotenv()  

# Google Cloud Natural Language API クライアントの初期化
nl_client = language_v1.LanguageServiceClient()

# 優先度計算ロジック（ai.py より）[cite: 1]
def calculate_priority_with_ml(ai_base_priority: int, likes: int, replies: int, impressions: int, is_complaint: bool) -> int:
    raw_features = np.array([[float(likes), float(replies), float(impressions)]])
    weights = np.array([0.4, 0.4, 0.2])
    
    engagement_score = np.dot(raw_features, weights)[0]
    engagement_boost = min(30.0, float(engagement_score) * 0.5)
    complaint_bonus = 15 if is_complaint else 0
    
    total_score = int((ai_base_priority * 0.6) + engagement_boost + complaint_bonus)
    return max(0, min(100, total_score))

# 1. Agents SDK のツール（Tool）としてデータベース分析処理を定義
@function_tool
def fetch_and_analyze_sns_posts(limit: int = 10) -> str:
    """Supabaseの sns_posts テーブルから投稿データを取得し、感情分析 (-0.3〜-0.6の建設的ゾーン判定) とML優先度計算を行ってデータベースに保存します。"""
    try:
        supabase = get_supabase_client()
        response = supabase.table("sns_posts").select("*").order("collected_at", desc=True).limit(limit).execute()
        posts = response.data

        if not posts:
            return "sns_posts テーブルにデータがありません。"

        processed_count = 0
        for post in posts:
            if not isinstance(post, dict):
                continue

            post_id = post.get("id")
            text = str(post.get("post_text") or "")
            
            raw_likes = post.get("likes_count", 0)
            likes = int(str(raw_likes)) if raw_likes is not None else 0
            raw_replies = post.get("replies_count", 0)
            replies = int(str(raw_replies)) if raw_replies is not None else 0

            # Google Cloud Natural Language API で感情分析を実行[cite: 1]
            document = language_v1.Document(
                content=text, 
                type_=language_v1.Document.Type.PLAIN_TEXT
            )
            sentiment_response = nl_client.analyze_sentiment(request={"document": document})
            sentiment = sentiment_response.document_sentiment
            cloud_sentiment_score = float(sentiment.score)       
            sentiment_magnitude = float(sentiment.magnitude)     
            
            # 建設的不満ゾーン (-0.3 ~ -0.6) の判定[cite: 1]
            is_target_sentiment_zone = (-0.6 <= cloud_sentiment_score <= -0.3)
            is_complaint = is_target_sentiment_zone  
            
            negativity = max(0.0, -cloud_sentiment_score) 
            magnitude_factor = min(10.0, sentiment_magnitude)
            
            base_priority = int((negativity * 70) + (magnitude_factor * 3))
            
            if is_target_sentiment_zone:
                base_priority += 15

            base_priority = max(10, min(100, base_priority))

            final_priority = calculate_priority_with_ml(
                ai_base_priority=base_priority,
                likes=likes,
                replies=replies,
                impressions=0,
                is_complaint=is_complaint
            )

            # Supabaseの ai_analysis_results テーブルへ保存[cite: 1]
            analysis_data = {
                "sns_post_id": post_id,
                "is_valid_issue": True,
                "sentiment_score": cloud_sentiment_score,
                "priority_score": final_priority
            }
            insert_ai_analysis(analysis_data)
            processed_count += 1

        return f"正常に {processed_count} 件のSNS投稿を分析し、Supabaseへ保存を完了しました。"

    except Exception as e:
        return f"分析処理中にエラーが発生しました: {e}"

# 2. OpenAI Agents SDK のエージェント定義
kobe_analysis_agent = Agent(
    name="神戸市SNS分析エージェント",
    instructions=(
        "あなたは神戸市の行政課題や市民の不満を分析する専門のAIエージェントです。"
        "ユーザーからの指示を受け取ったら、用意されているツール（fetch_and_analyze_sns_posts）を自律的に呼び出して、"
        "データベース上の投稿データの取得・感情分析・優先度スコア算出・保存の一連のプロセスを正確に実行してください。"
    ),
    model="gpt-4o",  # または利用するモデル名
    tools=[fetch_and_analyze_sns_posts]
)

if __name__ == "__main__":
    print("🤖 神戸市SNS分析エージェントを起動します...\n")
    
    # 3. エージェントの実行 (Runner)
    prompt = "最新のSNS投稿データを取得して感情分析と優先度計算を行い、結果をデータベースに保存してください。"
    result = Runner.run_sync(kobe_analysis_agent, prompt)
    
    print("\n--- エージェントの実行結果 ---")
    print(result.final_output)