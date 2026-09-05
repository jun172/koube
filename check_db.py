from database import get_supabase_client

def check_database():
    supabase = get_supabase_client()

    tables = [
        "sns_posts", 
        "ai_analysis_results", 
        "issue_catalog", 
        "issue_post_relations",
        "chat_histories"
    ]

    print("=== Supabase データベースデータ確認チェッカー ===\n")

    for table in tables:
        try:
            response = supabase.table(table).select("*", count="exact").execute()
            count = response.count if response.count is not None else len(response.data)
            print(f"📁 テーブル名: [{table}] (総件数: {count}件)")
            
            if response.data:
                # 最新の1件を表示
                print("   最新データ:")
                latest_item = response.data[-1]
                for key, value in latest_item.items():
                    print(f"     - {key}: {value}")
            else:
                print("   (データなし)")
            print("-" * 50)
            
        except Exception as e:
            print(f"❌ テーブル {table} の確認エラー: {e}\n")

if __name__ == "__main__":
    check_database()