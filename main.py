from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# ルーターをインポート (スペルを修正)
from routers import auth, posts, integration

app = FastAPI(
    title="Kobe API",
    description="Dチーム連携事業のためのサーバー", # カンマを追加
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

# ルーターの統合 (router プロパティを参照)
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(posts.router, prefix="/posts", tags=["Posts"])
app.include_router(integration.router, prefix="/integration", tags=["Kobesite Integration"])

@app.get("/")
async def root():
    return {"message": "Kobe API Server is running!"}