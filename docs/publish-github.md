# Publish to GitHub

From this repository:

```bash
gh auth login
gh repo create project-chats --public --source . --remote origin --push
```

If the repository already exists:

```bash
git remote add origin git@github.com:YOUR-USER/project-chats.git
git push -u origin main
```

Run tests before pushing:

```bash
python3 -m unittest discover -s tests
```
