#!/bin/bash
set -e # Fail if any of the commands below fail.

echo "Linting typescript & javascript..."
npx eslint src/web


echo "Building typescript..."
cd src/web && npx tsc && cd ../..