# koube
Kobesityとの連携事業のために開発します。
.envでの公開でのポストはやめてください。
.git clone してください

# 1. Gitの管理から .env を外す（ファイル自体は消えません）
git rm --cached .env

# 2. .gitignore ファイルを作成し、中身を記述する
echo ".env" >> .gitignore

# 3. 変更をコミットして反映させる
git add .gitignore
git commit -m "chore: remove .env from git and add to .gitignore"

# 4. GitHubへ反映
git push origin main