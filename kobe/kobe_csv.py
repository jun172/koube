import pandas as pd

# 1. データの読み込み
kobe_df = pd.read_csv('kobe.csv')
stations_df = pd.read_csv('kobe_stations.csv')

# 2. 町名（「区名+町名」）と駅名をそれぞれリスト化
towns = (kobe_df['city'] + kobe_df['town']).tolist()
stations = stations_df['station_name'].dropna().tolist()

# 3. 縦に合体させて重複を除去
master_keywords = list(set(towns + stations))

# 4. 1列のマスターCSVとして保存
df_master = pd.DataFrame({'keyword': master_keywords})
df_master.to_csv('kobe_keywords_master.csv', index=False, encoding='utf-8-sig')

print(f"キーワード総数: {len(df_master)}")