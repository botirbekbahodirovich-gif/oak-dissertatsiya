#!/bin/bash
cd /home/botirbek/oak-dissertatsiya
git add templates/base.html data.py
git commit -m 'feat: working AI chatbot with database queries'
echo "=== Git Status ===" >> /tmp/git_result.txt
git status >> /tmp/git_result.txt 2>&1
echo "=== Last Commit ===" >> /tmp/git_result.txt
git log --oneline -1 >> /tmp/git_result.txt 2>&1
