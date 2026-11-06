import json
import os
import subprocess
import sys
from dotenv import load_dotenv
import ollama

# ai (1).py と同様に database.py から Supabase 接続機能をインポート
from database import *

load_dotenv()  

# 1. システムプロンプト（AIへのルール指定）
SYSTEM_PROMPT = """あなたはSNSの投稿データを分析するデータアナリストです。
入力された投稿内容からスパムやノイズを判定し、政府・行政に対する国民の声や不満を適切に分類してください。

# 1. フィルタリング基準（is_valid の判定）
以下のいずれかに該当する場合は is_valid: false としてください。
- スパム・広告・勧誘・Bot投稿（アフィリエイトリンク、自動投稿、懸賞応募など）
- 意味をなさない文字列や暴言のみの投稿

# 2. 分類カテゴリ（category）※ is_valid が true の場合のみ指定
入力テキストがどの分野に関する声か、最も適切なものを1つ選んでください。
- 経済・物価（税金、物価高、給与、手当、景気など）
- 福祉・医療（年金、介護、医療費、子育て支援など）
- 外交・防衛（安全保障、外交政策、渡航関連など）
- インフラ・防災（交通、災害対応、環境、エネルギーなど）
- 政治・行政手続（選挙、議員の対応、行政のデジタル化・不祥事など）
- その他（上記に当てはまらない社会問題）

# 出力フォーマット
解説や前置き・挨拶は一切含めず、必ず以下のJSON配列（List）形式のみで出力してください。

[
  {
    "id": "ポストのID",
    "text": "入力テキスト内容",
    "is_valid": true,
    "category": "選択したカテゴリ名（is_validがfalseの場合はnull）"
  }
]
"""

def analyze_posts_with_ollama():
    try:
        # Supabase クライアントの取得
        supabase = get_supabase_client()
        
        # 2. Supabase の sns_posts テーブルからデータを取得
        response = supabase.table("sns_posts").select("id, post_text").order("collected_at", desc=True).limit(50).execute()
        posts = response.data

        if not posts:
            print("sns_posts にデータがありません。")
            return

        # Ollamaに渡すための入力データをJSON形式にまとめる
        input_data = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            input_data.append({
                "id": post.get("id"),
                "text": str(post.get("post_text") or "")
            })

        input_text = json.dumps(input_data, ensure_ascii=False, indent=2)

        # 3. messages の構築
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"以下の入力テキスト（JSON形式）を分析し、指定のJSON配列形式で出力してください。\n\n# 入力テキスト\n{input_text}"
                ),
            },
        ]

        print("Ollamaによる分析を実行中...")
        # 4. API / Ollama の実行
        response_ollama = ollama.chat(model="gemma4", messages=messages)
        output_content = response_ollama["message"]["content"]

        # LLM特有のマークダウン（```json ... ```）が含まれていた場合のクリーニング
        clean_content = output_content.strip()
        if clean_content.startswith("```json"):
            clean_content = clean_content[7:]
        if clean_content.startswith("```"):
            clean_content = clean_content[3:]
        if clean_content.endswith("```"):
            clean_content = clean_content[:-3]
        clean_content = clean_content.strip()

        analysis_results = json.loads(clean_content)

        # 5. 分析結果の処理（必要に応じて Supabase への保存処理などを追加可能）
        success_count = 0
        for item in analysis_results:
            post_id = item.get("id")
            is_valid = item.get("is_valid", True)
            category = item.get("category")

            if not is_valid:
                continue

            success_count += 1
            print(f"ID: {post_id} -> カテゴリ: {category}")

        # 分析結果をローカルファイルにもバックアップとして保存
        output_file_path = "sns_cat_fil_result.json"
        with open(output_file_path, "w", encoding="utf-8") as f:
            f.write(clean_content)

        print(f"-------------------\n分析完了！結果を {output_file_path} に保存しました（有効件数: {success_count}件）。\n-------------------")

    except Exception as e:
        print(f"処理中にエラーが発生しました: {e}")

    finally:
        # 6. Ollamaプロセスのクリーンアップ
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/f", "/im", "ollama.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                subprocess.run(["pkill", "ollama"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            print("Ollamaプロセスを終了しました。\n-------------------")
        except Exception as e:
            print(f"Ollamaプロセスの終了時にエラーが発生しました: {e}\n-------------------")

if __name__ == "__main__":
    analyze_posts_with_ollama()