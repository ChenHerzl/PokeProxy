#!/bin/sh
# Run from the repository root. Isolated generator; does not change uv.lock.
set -eu
uv tool run --from grpcio-tools==1.74.0 python -m grpc_tools.protoc \
  --proto_path=proto \
  --python_out=src/pokeproxy/proto \
  --pyi_out=src/pokeproxy/proto \
  proto/pokemon.proto
