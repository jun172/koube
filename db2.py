from kobe.database import get_supabase_client

supabase = get_supabase_client()

print("1. sns_posts のデータ")
posts = supabase.table("sns_posts").select("*").execute()
print(posts.data)

print("\n--- 2. ai_analysis_results のデータ ---")
analysis = supabase.table("ai_analysis_results").select("*").execute()
print(analysis.data)

print("\n--- 3. issue_catalog のデータ ---")
catalog = supabase.table("issue_catalog").select("*").execute()
print(catalog.data)

print("\n--- 4. issue_post_relations のデータ ---")
relations = supabase.table("issue_post_relations").select("*").execute()
print(relations.data)