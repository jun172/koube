import os
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
from tweepy import Client
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# クライアントの初期化
x_client = Client(bearer_token=os.environ.get("X_BEARER_TOKEN"))
supabase = create_client(os.environ.get("SUPABASE_URL"),os.environ.get("SUPABASE_KEY"))

class SearchRequest(BaseModel):
    keyword: str
    
@router.post("/collect")
async def collect_posts(request: SearchRequest):
    try:
        # 1. Xから投稿を取得
        