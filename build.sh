#!/usr/bin/env bash
set -euo pipefail

# The lock is resolved for aarch64 only and the pip call below hard-targets it,
# so this builds the arm64 Lambda bundle and nothing else. It used to take an
# arch argument; against a target-pinned lock that could only ever produce an
# arm64 bundle inside an amd64 container.
PLATFORM="linux/arm64"
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
    # Flags mirror scripts/compile_requirements.sh and deploy.yml: the lock was
    # resolved for this exact target, so pinning it here makes the local bundle
    # hold the same distributions, from the same wheels, as the one CI builds.
    # (The zips themselves are not byte-identical -- this script and the deploy
    # job use different zip tools and neither normalizes mtimes.)
    python -m pip install --no-deps -r requirements.lock -t ${BUILD_DIR} --no-cache-dir \
      --platform manylinux2014_aarch64 \
      --python-version 3.12 \
      --implementation cp \
      --abi cp312 \
      --only-binary=:all:
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
