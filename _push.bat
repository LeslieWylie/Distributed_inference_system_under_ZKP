@echo off
cd /d C:\ZKP
git rm -r --cached data/ 2>nul
git rm -r --cached models/.mnist_cache/ 2>nul
git rm -r --cached models/.mnist_data/ 2>nul
git rm --cached "中期进展情况检查.docx" 2>nul
git rm --cached _git_clean_and_push.ps1 2>nul
git add --all
git commit -m "chore: remove binary data from tracking"
git push gitee master --force
echo PUSH_COMPLETE
