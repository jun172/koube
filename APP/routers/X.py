from fastapi import APIRouter
import tweepy
import os

router = APIRouter()

# クライアントは外側で一度だけ準備する
# ※bearer_tokenは環境変数から取得するようにしましょう（セキュリティ対策）
client = tweepy.Client(bearer_token=os.environ.get("X_BEARER_TOKEN"))

@router.get("/collect")
async def collect_tweets():
    """
    フロントエンドから叩かれるとツイートを取得するエンドポイント
    """
    query = "神戸市 (不満 OR 最悪 OR 不便 OR 問題 OR 困る) -is:retweet lang:ja"
    
    try:
        response = client.search_recent_tweets(
            query=query,
            max_results=10, 
            tweet_fields=["created_at", "text"]
        )
        
        if not response.data:
            return {"message": "該当する投稿が見つかりませんでした"}
            
        # 取得したデータをリストにして返す
        results = [{"created_at": str(t.created_at), "text": t.text} for t in response.data]
        return {"count": len(results), "data": results}
        
    except Exception as e:
        return {"error": str(e)}