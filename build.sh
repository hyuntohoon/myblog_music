#!/usr/bin/env bash
set -euo pipefail

# Usage: ./build_lambda.sh [arch]
# Example: ./build_lambda.sh arm64  (or amd64)
ARCH="${1:-arm64}"
PLATFORM="linux/${ARCH}"
: "${SHARED_DB_PAT:?SHARED_DB_PAT is required for the private shared_db package}"

# 빌드 결과물: ./lambda_bundle.zip
OUTPUT_ZIP="lambda_bundle.zip"
BUILD_DIR="lambda_build"

echo "🚀 Building FastAPI Lambda bundle for platform=${PLATFORM}"
rm -rf "${BUILD_DIR}" "${OUTPUT_ZIP}"
mkdir -p "${BUILD_DIR}"

# Docker 기반으로 AWS Lambda Python 3.12 환경 맞춰 빌드
docker run --rm \
  --platform "${PLATFORM}" \
  -e SHARED_DB_PAT \
  -v "$PWD":/var/task \
  -w /var/task \
  --entrypoint /bin/bash \
  public.ecr.aws/lambda/python:3.12 \
  -lc "
    echo '== Step 1: pip install =='
    git config --global url.\"https://\${SHARED_DB_PAT}@github.com/\".insteadOf \"https://github.com/\"
    python -m pip install --no-deps -r requirements.lock -t ${BUILD_DIR} --no-cache-dir --only-binary=:all:
    echo '== Step 2: copy app files =='
    cp -r app ${BUILD_DIR}/
    echo '== Step 3: cleanup =='
    find ${BUILD_DIR} -type d -name '__pycache__' -exec rm -rf {} +
    find ${BUILD_DIR} -name '*.pyc' -delete
    echo '== Step 4: zip bundle =='
    cd ${BUILD_DIR} && python -m zipfile -c ../${OUTPUT_ZIP} . && cd -
    echo '✅ Build complete: ${OUTPUT_ZIP}'
  "

rm -rf "${BUILD_DIR}"
ls -lh "${OUTPUT_ZIP}"
echo "✅ Lambda bundle ready: ${OUTPUT_ZIP}"
