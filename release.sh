#!/usr/bin/env bash
set -euo pipefail

# Usage: ./release.sh <version>
# Example: ./release.sh 0.2.0
#
# Bumps version in all 3 files, commits, tags, and pushes.
# Then create a GitHub Release at the printed URL to trigger PyPI publish.

VERSION="${1:?Usage: ./release.sh <version>}"

# Validate no uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Uncommitted changes detected. Commit or stash first."
    exit 1
fi

# Validate tag doesn't exist
if git rev-parse "v${VERSION}" >/dev/null 2>&1; then
    echo "ERROR: Tag v${VERSION} already exists."
    exit 1
fi

echo "Bumping to version ${VERSION}..."

# 1. pyproject.toml
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
grep -q "version = \"${VERSION}\"" pyproject.toml || { echo "FAILED: pyproject.toml"; exit 1; }

# 2. __init__.py
sed -i "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" notion_mcp/__init__.py
grep -q "__version__ = \"${VERSION}\"" notion_mcp/__init__.py || { echo "FAILED: __init__.py"; exit 1; }

# 3. server.json (both occurrences)
sed -i "s/\"version\": \"[^\"]*\"/\"version\": \"${VERSION}\"/g" server.json
grep -c "\"version\": \"${VERSION}\"" server.json | grep -q "2" || { echo "FAILED: server.json"; exit 1; }

echo "Version bumped in all 3 files."

# Commit, tag, push
git add pyproject.toml notion_mcp/__init__.py server.json
git commit -m "Release v${VERSION}"
git tag "v${VERSION}"
git push origin main --tags

REPO_URL=$(git remote get-url origin | sed 's/\.git$//' | sed 's|git@github.com:|https://github.com/|')
echo ""
RELEASE_URL="${REPO_URL}/releases/new?tag=v${VERSION}&title=v${VERSION}&generate-notes=true"
echo "Done! Create the GitHub Release to trigger PyPI publish:"
echo "  ${RELEASE_URL}"
xdg-open "${RELEASE_URL}" 2>/dev/null || open "${RELEASE_URL}" 2>/dev/null || true
