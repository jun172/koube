import os
from fastapi import APIRouter,HTTPException
from supabase import create_client
from dotenv import load_dotenv
from pydantic import BaseModel
# .envファイルを読み込む
load_dotenv()

router = APIRouter()

# 環境変数を取得
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")

# Noneチェック：もし環境変数が設定されていなければ例外を発生させる
if supabase_url is None or supabase_key is None:
    raise RuntimeError("環境変数 SUPABASE_URL と SUPABASE_KEY が .env に設定されていません")

# ここまで来れば supabase_url と supabase_key は確実に文字列(str)だと判断されます
supabase = create_client(supabase_url, supabase_key)

@router.get("/test")
async def test_auth():
    return {"message": "Supabase connection is ready!"}

# ログイン時に受け取るデータの形を定義
class LoginRequest(BaseModel):
    email: str
    password: str
    
router = APIRouter()

# クライアントの初期化（前回のコードの続き）
@router.post("/login")
async def login(requests: LoginRequest):
    try:
        #Supabaseの認証APIを呼び出す
        auth_response = supabase.auth.sign_in_with_password({
            "email":requests.email,
            "password":requests.password
        })
        # 成功したらユーザー情報を返す
        return {"message":"ログイン成功","user":auth_response.user.email}
    except Exception as e:
        ## 失敗したらエラーを返す
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")