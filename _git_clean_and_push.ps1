Set-Location C:\ZKP
$env:GIT_TERMINAL_PROMPT = "0"

# Remove binary/data files from index
git rm -r --cached data/ 2>$null
git rm -r --cached models/.mnist_cache/ 2>$null
git rm -r --cached models/.mnist_data/ 2>$null
git rm --cached "中期进展情况检查.docx" 2>$null

# Re-add respecting updated .gitignore
git add --all

# Commit
git commit -m "docs: update all documentation to current state (CNN, scalability, linking conclusions, canonical handoff)"

# Push to gitee
git push gitee master

Write-Output "DONE"
