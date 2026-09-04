from database import get_supabase_client

supabase = get_supabase_client()

tables = [
    "sns_posts", 
    "ai_analysis_results", 
    "issue_catalog", 
    "issue_post_relations"
]

for table in tables:
    try:
        response = supabase.table(table).select("*", count="exact").execute()
        print(f"--- テーブル名: {table} (総件数: {response.count}) ---")
        if response.data:
            # 最新の1件を表示
            print(response.data[-1])
        else:
            print("データはまだありません。")
        print("\n")
    except Exception as e:
        print(f"テーブル {table} の確認エラー: {e}")