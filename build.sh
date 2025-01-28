#!/bin/bash

# ---
# Ensure a venv with build and twine is active. along with
# other depedencies that are required for this library,
# as listed in requirements.txt
# Also ensure PyPi token exists in .pypi_token file

# Run with: ./build.sh <version_number>
# ---

# Enable debug mode to print each command as it is executed
set -xe

# Check if a version number is provided as an argument
if [ $# -eq 0 ]; then
    echo "Usage: $0 <version_number>"
    exit 1
fi

VERSION=$1

rm -rf ./dist

# Detect OS type (Linux or Darwin/macOS)
# and set the appropriate sed in-place editing option
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS (BSD sed)
    sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
else
    # Linux or other (GNU sed usually)
    sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
fi

# Build the library
python3 -m build


# Upload to PyPI
TWINE_USERNAME="__token__" TWINE_PASSWORD=$(cat .pypi_token) twine upload dist/*
