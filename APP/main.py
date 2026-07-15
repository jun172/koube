from  fastapi import FastAPI
from APP.routers import X
from routers import Ai
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import os
app = FastAPI(
    title="Unmute City API",
    description="市民の声から社会課題を抽出する安全なパイプライン"
)

# ルーターの登録
# prefixをつけることで、URLが整理されます
# main.py の修正案
app.include_router(X.router, prefix="/api/sns", tags=["X"])
app.include_router(Ai.router, prefix="/api/ai", tags=["AI"])
#.endファイルを読み込む
load_dotenv()
# 環境変数から読み込む（ローカルでは開発用URL、本番ではドメイン）
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
# ReactのURL
origins=[
    "http://localhost:5173",#リアクトでの内部APIを使う
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET","POST"],          # 全てのメソッド（GET, POSTなど）を許可
    allow_headers=["content_Type","Authorization"],          # 全てのヘッダーを許可
)

@app.get("/")
async def root():
    return {"message": "Unmute City API is running"}